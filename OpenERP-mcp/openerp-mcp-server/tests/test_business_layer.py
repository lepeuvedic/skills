"""Tests for the neutral business layer: hooks, locking, transactions.

These run offline against a fake OpenERP connection -- no live server required.
"""

import pytest

from openerp_mcp.business import (
    BusinessLayer,
    Operation,
    OpType,
    PolicyHook,
    PolicyViolation,
    TransactionStep,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeModel:
    def __init__(self, name, store):
        self.name = name
        self.store = store

    def search(self, domain, offset=0, limit=False, order=False, context=None):
        return list(self.store.get(self.name, {}).keys())

    def read(self, ids, fields=None, context=None):
        recs = self.store.get(self.name, {})
        ids = ids if isinstance(ids, (list, tuple)) else [ids]
        return [dict(recs[i], id=i) for i in ids if i in recs]

    def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None, context=None):
        recs = self.store.get(self.name, {})
        out = []
        # crude domain support: [["code","in",[...]]]
        wanted = None
        if domain and isinstance(domain[0], list) and domain[0][1] == "in":
            wanted = set(domain[0][2])
        for i, rec in recs.items():
            if wanted is not None and rec.get("code") not in wanted:
                continue
            out.append(dict(rec, id=i))
        return out

    def create(self, values, context=None):
        recs = self.store.setdefault(self.name, {})
        new_id = (max(recs) + 1) if recs else 1
        recs[new_id] = dict(values)
        return new_id

    def write(self, ids, values, context=None):
        recs = self.store.setdefault(self.name, {})
        for i in ids:
            recs.setdefault(i, {}).update(values)
        return True

    def unlink(self, ids, context=None):
        recs = self.store.setdefault(self.name, {})
        for i in ids:
            recs.pop(i, None)
        return True

    def fields_get(self, allfields, attributes):
        return {"name": {"type": "char", "string": "Name"}}


class FakeConnection:
    def __init__(self, store, user_id=7):
        self.store = store
        self.user_id = user_id

    def get_model(self, name):
        return FakeModel(name, self.store)


@pytest.fixture
def store():
    return {
        "account.account": {10: {"code": "411000", "name": "Clients"},
                            11: {"code": "512000", "name": "Banque"}},
        "account.journal": {20: {"code": "VTE", "name": "Ventes"},
                            21: {"code": "BNK", "name": "Banque"}},
        "account.move": {},
        "res.partner": {1: {"name": "ACME"}},
    }


# --------------------------------------------------------------------------- #
# Neutral by default
# --------------------------------------------------------------------------- #
def test_neutral_layer_allows_everything(store):
    layer = BusinessLayer(FakeConnection(store))  # no locks
    assert layer.create("res.partner", {"name": "New"}) == 2
    assert layer.write("res.partner", [1], {"name": "ACME2"}) is True
    assert layer.search_read("res.partner")  # reads work
    assert layer.locked_summary()["locked_accounts"] == []
    assert layer.locked_summary()["locked_journals"] == []


# --------------------------------------------------------------------------- #
# Validation hooks
# --------------------------------------------------------------------------- #
def test_custom_hook_can_veto(store):
    class NoDeletePartners(PolicyHook):
        name = "no-delete-partners"

        def before(self, op: Operation, ctx) -> None:
            if op.op_type == OpType.UNLINK and op.model == "res.partner":
                raise PolicyViolation("Deleting partners is not allowed.")

    layer = BusinessLayer(FakeConnection(store))
    layer.add_hook(NoDeletePartners())
    with pytest.raises(PolicyViolation):
        layer.unlink("res.partner", [1])
    # other ops still fine
    assert layer.write("res.partner", [1], {"name": "X"}) is True


def test_after_hook_does_not_break_call(store):
    seen = []

    class AuditHook(PolicyHook):
        name = "audit"

        def after(self, op, result, ctx):
            seen.append((op.op_type, result))
            raise RuntimeError("after-hooks must be swallowed")

    layer = BusinessLayer(FakeConnection(store))
    layer.add_hook(AuditHook())
    assert layer.create("res.partner", {"name": "Z"}) == 2
    assert seen and seen[0][0] == OpType.CREATE


# --------------------------------------------------------------------------- #
# Account / journal locking
# --------------------------------------------------------------------------- #
def test_locked_account_blocks_write(store):
    layer = BusinessLayer(FakeConnection(store), locked_accounts=["411000"])
    # account id 10 has code 411000 -> locked
    with pytest.raises(PolicyViolation):
        layer.write("account.account", [10], {"name": "renamed"})
    # the other account is editable
    assert layer.write("account.account", [11], {"name": "ok"}) is True


def test_locked_journal_blocks_move_posting(store):
    layer = BusinessLayer(FakeConnection(store), locked_journals=["VTE"])
    # creating a move referencing locked journal 20 -> blocked
    with pytest.raises(PolicyViolation):
        layer.create("account.move", {"journal_id": 20, "ref": "INV/1"})
    # a move in the unlocked journal is fine
    assert isinstance(layer.create("account.move", {"journal_id": 21}), int)


def test_locked_account_read_is_allowed(store):
    layer = BusinessLayer(FakeConnection(store), locked_accounts=["411000"])
    assert layer.read("account.account", [10])  # reads never blocked


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #
def test_transaction_commits_in_order(store):
    layer = BusinessLayer(FakeConnection(store))
    txn = layer.transaction("create-two-partners")
    txn.add(TransactionStep("p1", lambda: layer.create("res.partner", {"name": "A"})))
    txn.add(TransactionStep("p2", lambda: layer.create("res.partner", {"name": "B"})))
    results = txn.execute()
    assert results == [2, 3]


def test_transaction_compensates_on_failure(store):
    layer = BusinessLayer(FakeConnection(store))
    created = {}

    def make_partner(name):
        pid = layer.create("res.partner", {"name": name})
        created[name] = pid
        return pid

    txn = layer.transaction("rollback-demo")
    txn.add(TransactionStep(
        "create A",
        run=lambda: make_partner("A"),
        compensate=lambda pid: layer.unlink("res.partner", [pid]),
    ))
    txn.add(TransactionStep(
        "boom",
        run=lambda: (_ for _ in ()).throw(RuntimeError("explode")),
    ))

    with pytest.raises(RuntimeError):
        txn.execute()
    # The created partner A must have been compensated (deleted).
    assert created["A"] not in store["res.partner"]
