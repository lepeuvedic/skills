"""Neutral business layer over the OpenERP model abstraction.

Purpose
-------
The MCP tools must keep the *simple* model-based interface (``search_read``, ``read``,
``create``, ``write``, ``unlink``, ``call``), but every call is routed through this layer
so that, later, accounting guarantees can be enforced in **one** place instead of being
scattered (and therefore trivially bypassed) across the raw model API.

Today this layer is **neutral**: the default policy allows everything. What it already
provides:

* **Validation hooks** -- :class:`PolicyHook` objects inspect every operation *before*
  (and optionally *after*) it runs and may veto it. Register your own to encode rules.
* **A locked-accounts / locked-journals registry** -- :class:`AccountGuard` can mark
  specific ``account.account`` codes and ``account.journal`` codes as read-only. The guard
  is wired in but ships with empty (or env-configured) lists, so it is a no-op until you
  populate it.
* **A transaction context** -- :meth:`BusinessLayer.transaction` groups several operations
  into a unit, runs all pre-hooks first, executes them in order, and records what happened.
  OpenERP's XML-RPC API is not transactional across calls, so this provides *application-
  level* sequencing plus an optional best-effort compensation step, not a database ROLLBACK.

Why route through here instead of exposing models directly
----------------------------------------------------------
If MCP tools called ``connection.get_model(...)`` directly, any future locking rule could be
sidestepped by simply asking for a raw model write. By making :class:`BusinessLayer` the sole
gateway and giving it the locking/validation hooks, the locks live at the gateway and cannot
be skipped without editing this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence

_logger = logging.getLogger("openerp_mcp.business")


# --------------------------------------------------------------------------- #
# Operation model
# --------------------------------------------------------------------------- #
class OpType(str, Enum):
    """The kind of model operation being requested."""

    SEARCH = "search"
    READ = "read"
    SEARCH_READ = "search_read"
    CREATE = "create"
    WRITE = "write"
    UNLINK = "unlink"
    CALL = "call"  # arbitrary model method


# Operations that mutate data; locking/validation focuses on these.
WRITE_OPS = {OpType.CREATE, OpType.WRITE, OpType.UNLINK}


@dataclass
class Operation:
    """A single requested model operation, passed to hooks before execution."""

    op_type: OpType
    model: str
    method: Optional[str] = None          # for OpType.CALL
    ids: Optional[Sequence[int]] = None   # records targeted (write/unlink/read)
    domain: Optional[list] = None         # for search / search_read
    values: Optional[Dict[str, Any]] = None  # for create / write
    args: tuple = ()                      # positional args for CALL
    kwargs: Dict[str, Any] = field(default_factory=dict)

    def is_write(self) -> bool:
        return self.op_type in WRITE_OPS


class PolicyViolation(RuntimeError):
    """Raised by a hook (or the guard) to veto an operation.

    The message is surfaced to the MCP client, so it should be actionable.
    """


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #
class PolicyHook:
    """Base class for validation / policy hooks.

    Subclass and override :meth:`before` (and optionally :meth:`after`). Raise
    :class:`PolicyViolation` from :meth:`before` to veto an operation. The default
    implementation is a no-op, which keeps the layer neutral.
    """

    name: str = "policy-hook"

    def before(self, op: Operation, ctx: "BusinessLayer") -> None:
        """Called before ``op`` executes. Raise PolicyViolation to block it."""

    def after(self, op: Operation, result: Any, ctx: "BusinessLayer") -> None:
        """Called after ``op`` executes successfully. For audit/side effects only."""


class CallableHook(PolicyHook):
    """Adapter so a plain function can be registered as a hook."""

    def __init__(self, fn: Callable[[Operation, "BusinessLayer"], None], name: str = "callable-hook"):
        self._fn = fn
        self.name = name

    def before(self, op: Operation, ctx: "BusinessLayer") -> None:
        self._fn(op, ctx)


# --------------------------------------------------------------------------- #
# Account / journal guard (locking registry)
# --------------------------------------------------------------------------- #
class AccountGuard(PolicyHook):
    """Read-only locking for specific accounts and journals.

    Neutral by default: with empty lists it allows everything. Populate
    ``locked_account_codes`` / ``locked_journal_codes`` (e.g. from the environment) to make
    those accounts/journals refuse mutations.

    The guard resolves codes to ids lazily and caches them, so it works against OpenERP 7's
    ``account.account`` / ``account.journal`` models without an extra round-trip per call once
    warmed.

    Scope of enforcement (intentionally explicit, so it cannot silently miss):
      * ``account.account`` create/write/unlink touching a locked account code -> blocked.
      * ``account.journal`` create/write/unlink touching a locked journal code -> blocked.
      * ``account.move`` / ``account.move.line`` create/write/unlink referencing a locked
        journal (``journal_id``) or locked account (``account_id``) -> blocked.

    This is a starting point. As your accounting rules firm up, extend
    :meth:`_violates` rather than adding ad-hoc checks elsewhere.
    """

    name = "account-guard"

    # Models whose records carry a journal/account reference we should inspect.
    JOURNAL_REF_MODELS = {"account.move": "journal_id", "account.move.line": "journal_id"}
    ACCOUNT_REF_MODELS = {"account.move.line": "account_id"}

    def __init__(self, locked_account_codes: Sequence[str] = (),
                 locked_journal_codes: Sequence[str] = ()):
        self.locked_account_codes = {c for c in locked_account_codes if c}
        self.locked_journal_codes = {c for c in locked_journal_codes if c}
        self._account_ids: Optional[set] = None
        self._journal_ids: Optional[set] = None

    # -- lazy id resolution -------------------------------------------------- #
    def _warm(self, ctx: "BusinessLayer") -> None:
        if self._account_ids is None:
            self._account_ids = set()
            if self.locked_account_codes:
                recs = ctx._raw_model("account.account").search_read(
                    [["code", "in", list(self.locked_account_codes)]], ["id", "code"]
                )
                self._account_ids = {r["id"] for r in recs}
        if self._journal_ids is None:
            self._journal_ids = set()
            if self.locked_journal_codes:
                recs = ctx._raw_model("account.journal").search_read(
                    [["code", "in", list(self.locked_journal_codes)]], ["id", "code"]
                )
                self._journal_ids = {r["id"] for r in recs}

    # -- decision ------------------------------------------------------------ #
    def _violates(self, op: Operation, ctx: "BusinessLayer") -> Optional[str]:
        if not (self.locked_account_codes or self.locked_journal_codes):
            return None
        if not op.is_write():
            return None

        self._warm(ctx)

        # Direct edits to a locked account/journal record.
        if op.model == "account.account" and self._account_ids:
            if self._touches(op, self._account_ids):
                return "One or more targeted account.account records are locked (read-only)."
        if op.model == "account.journal" and self._journal_ids:
            if self._touches(op, self._journal_ids):
                return "One or more targeted account.journal records are locked (read-only)."

        # Edits to moves/lines that reference a locked journal/account.
        jref = self.JOURNAL_REF_MODELS.get(op.model)
        if jref and self._journal_ids and op.values and op.values.get(jref) in self._journal_ids:
            return (
                f"Operation references a locked journal via '{jref}'. "
                "Posting to this journal is restricted."
            )
        aref = self.ACCOUNT_REF_MODELS.get(op.model)
        if aref and self._account_ids and op.values and op.values.get(aref) in self._account_ids:
            return (
                f"Operation references a locked account via '{aref}'. "
                "Posting to this account is restricted."
            )
        return None

    @staticmethod
    def _touches(op: Operation, locked_ids: set) -> bool:
        if op.op_type == OpType.CREATE:
            # Creating a brand-new locked record is allowed; locking is about edits.
            return False
        if op.ids:
            return any(i in locked_ids for i in op.ids)
        return False

    def before(self, op: Operation, ctx: "BusinessLayer") -> None:
        reason = self._violates(op, ctx)
        if reason:
            raise PolicyViolation(reason)


# --------------------------------------------------------------------------- #
# Business layer
# --------------------------------------------------------------------------- #
class TransactionStep:
    """One step inside a transaction: a thunk plus an optional compensation."""

    def __init__(self, describe: str, run: Callable[[], Any],
                 compensate: Optional[Callable[[Any], None]] = None):
        self.describe = describe
        self.run = run
        self.compensate = compensate


class Transaction:
    """A best-effort, application-level multi-operation unit.

    OpenERP's XML-RPC API has no cross-call DB transaction, so this is *not* an ACID
    rollback. It guarantees: (1) all registered pre-hooks pass before any step executes,
    (2) steps run in order, and (3) if a later step fails, completed steps with a
    ``compensate`` callback are undone in reverse order (best effort).
    """

    def __init__(self, layer: "BusinessLayer", label: str):
        self._layer = layer
        self.label = label
        self._steps: List[TransactionStep] = []
        self.results: List[Any] = []

    def add(self, step: TransactionStep) -> "Transaction":
        self._steps.append(step)
        return self

    def execute(self) -> List[Any]:
        done: List[tuple] = []  # (step, result)
        try:
            for step in self._steps:
                result = step.run()
                self.results.append(result)
                done.append((step, result))
            _logger.info("Transaction '%s' committed (%d steps).", self.label, len(done))
            return self.results
        except Exception as exc:
            _logger.error("Transaction '%s' failed at step %d: %s", self.label, len(done), exc)
            for step, result in reversed(done):
                if step.compensate:
                    try:
                        step.compensate(result)
                        _logger.info("Compensated step: %s", step.describe)
                    except Exception as cexc:  # pragma: no cover - defensive
                        _logger.error("Compensation failed for '%s': %s", step.describe, cexc)
            raise


class BusinessLayer:
    """Sole gateway between MCP tools and the OpenERP models.

    Neutral by default. Register :class:`PolicyHook` instances to add validation, and pass
    locked account/journal codes to enable the :class:`AccountGuard`.
    """

    def __init__(self, connection, locked_accounts: Sequence[str] = (),
                 locked_journals: Sequence[str] = ()):
        self._connection = connection
        self._model_cache: Dict[str, Any] = {}
        self._hooks: List[PolicyHook] = []

        # The guard is always installed; with empty lists it is a no-op (neutral).
        self.guard = AccountGuard(locked_accounts, locked_journals)
        self.add_hook(self.guard)

    # -- hook management ----------------------------------------------------- #
    def add_hook(self, hook: PolicyHook) -> None:
        self._hooks.append(hook)
        _logger.debug("Registered policy hook: %s", hook.name)

    def add_callable_hook(self, fn: Callable[[Operation, "BusinessLayer"], None],
                          name: str = "callable-hook") -> None:
        self.add_hook(CallableHook(fn, name))

    # -- raw model access (private; bypasses hooks; used by hooks themselves) - #
    def _raw_model(self, model: str):
        if model not in self._model_cache:
            self._model_cache[model] = self._connection.get_model(model)
        return self._model_cache[model]

    # -- hook dispatch ------------------------------------------------------- #
    def _run_before(self, op: Operation) -> None:
        for hook in self._hooks:
            hook.before(op, self)

    def _run_after(self, op: Operation, result: Any) -> None:
        for hook in self._hooks:
            try:
                hook.after(op, result, self)
            except Exception as exc:  # after-hooks must not break the call
                _logger.error("after-hook '%s' raised (ignored): %s", hook.name, exc)

    def _dispatch(self, op: Operation, runner: Callable[[], Any]) -> Any:
        self._run_before(op)
        result = runner()
        self._run_after(op, result)
        return result

    # -- guarded model operations (the public surface tools use) ------------- #
    def search(self, model: str, domain=None, offset=0, limit=None, order=None, context=None):
        op = Operation(OpType.SEARCH, model, domain=domain or [])
        m = self._raw_model(model)
        return self._dispatch(
            op,
            lambda: m.search(domain or [], offset, limit or False, order or False,
                             context=context or {}),
        )

    def read(self, model: str, ids, fields=None, context=None):
        ids_list = ids if isinstance(ids, (list, tuple)) else [ids]
        op = Operation(OpType.READ, model, ids=list(ids_list))
        m = self._raw_model(model)
        return self._dispatch(op, lambda: m.read(ids, fields or [], context=context or {}))

    def search_read(self, model: str, domain=None, fields=None, offset=0, limit=None,
                    order=None, context=None):
        op = Operation(OpType.SEARCH_READ, model, domain=domain or [])
        m = self._raw_model(model)
        return self._dispatch(
            op,
            lambda: m.search_read(domain or [], fields or [], offset, limit, order,
                                  context=context or {}),
        )

    def create(self, model: str, values: Dict[str, Any], context=None):
        op = Operation(OpType.CREATE, model, values=dict(values))
        m = self._raw_model(model)
        return self._dispatch(op, lambda: m.create(values, context=context or {}))

    def write(self, model: str, ids, values: Dict[str, Any], context=None):
        ids_list = ids if isinstance(ids, (list, tuple)) else [ids]
        op = Operation(OpType.WRITE, model, ids=list(ids_list), values=dict(values))
        m = self._raw_model(model)
        return self._dispatch(op, lambda: m.write(list(ids_list), values, context=context or {}))

    def unlink(self, model: str, ids, context=None):
        ids_list = ids if isinstance(ids, (list, tuple)) else [ids]
        op = Operation(OpType.UNLINK, model, ids=list(ids_list))
        m = self._raw_model(model)
        return self._dispatch(op, lambda: m.unlink(list(ids_list), context=context or {}))

    def call(self, model: str, method: str, *args, **kwargs):
        op = Operation(OpType.CALL, model, method=method, args=args, kwargs=kwargs)
        m = self._raw_model(model)
        return self._dispatch(op, lambda: getattr(m, method)(*args, **kwargs))

    # -- transactions -------------------------------------------------------- #
    def transaction(self, label: str) -> Transaction:
        """Start a new multi-operation transaction. See :class:`Transaction`."""
        return Transaction(self, label)

    # -- introspection ------------------------------------------------------- #
    @property
    def user_id(self):
        return self._connection.user_id

    def locked_summary(self) -> dict:
        return {
            "locked_accounts": sorted(self.guard.locked_account_codes),
            "locked_journals": sorted(self.guard.locked_journal_codes),
            "hooks": [h.name for h in self._hooks],
        }
