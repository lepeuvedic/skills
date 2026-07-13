#!/usr/bin/env node
/**
 * Serveur MCP - outil unique "move_download".
 *
 * Déplace un fichier depuis %USERPROFILE%/Downloads vers un dossier choisi
 * par le LLM, à condition que :
 *   1. le nom de fichier corresponde EXACTEMENT à un fichier présent dans Downloads,
 *   2. ce fichier ait été écrit (mtime) il y a moins de 5 minutes.
 *
 * Objectif : permettre à un LLM de récupérer un fichier qu'un outil externe
 * (navigateur, etc.) vient de déposer dans Downloads, sans lui donner un accès
 * large au système de fichiers de l'utilisateur.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

const MAX_AGE_MS = 5 * 60 * 1000; // 5 minutes
const DOWNLOADS_DIR = path.join(os.homedir(), "Downloads");

const TOOL_NAME = "move_download";

const server = new Server(
  {
    name: "mcp-move-download",
    version: "1.0.0",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: TOOL_NAME,
      description:
        "Déplace un fichier récemment déposé dans %USERPROFILE%/Downloads vers un dossier choisi par le LLM. " +
        "Le nom du fichier doit correspondre exactement à un fichier présent dans Downloads, " +
        "et ce fichier doit avoir été écrit il y a moins de 5 minutes. Sinon l'outil échoue.",
      inputSchema: {
        type: "object",
        properties: {
          filename: {
            type: "string",
            description:
              "Nom exact du fichier dans %USERPROFILE%/Downloads (avec extension, sans chemin).",
          },
          destination: {
            type: "string",
            description:
              "Chemin absolu du dossier de destination. Il sera créé s'il n'existe pas.",
          },
        },
        required: ["filename", "destination"],
        additionalProperties: false,
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  if (request.params.name !== TOOL_NAME) {
    throw new Error(`Outil inconnu : ${request.params.name}`);
  }

  const args = request.params.arguments ?? {};
  const { filename, destination } = args;

  try {
    return await moveDownload(filename, destination);
  } catch (err) {
    return {
      isError: true,
      content: [{ type: "text", text: `Erreur : ${err.message}` }],
    };
  }
});

async function moveDownload(filename, destination) {
  // --- Validation des arguments ---
  if (typeof filename !== "string" || filename.length === 0) {
    throw new Error("Le paramètre 'filename' est requis et doit être une chaîne non vide.");
  }
  if (typeof destination !== "string" || destination.length === 0) {
    throw new Error("Le paramètre 'destination' est requis et doit être une chaîne non vide.");
  }
  // Empêche toute traversée de chemin (../, sous-dossiers, chemin absolu déguisé).
  if (path.basename(filename) !== filename) {
    throw new Error(
      "'filename' doit être un simple nom de fichier (sans chemin ni '..')."
    );
  }
  if (!path.isAbsolute(destination)) {
    throw new Error("'destination' doit être un chemin absolu.");
  }

  const sourcePath = path.join(DOWNLOADS_DIR, filename);

  // --- Vérifie que le fichier existe exactement dans Downloads ---
  // On relit le dossier pour garantir une correspondance de nom EXACTE
  // (et éviter les surprises de casse selon le système de fichiers).
  let entries;
  try {
    entries = await fs.readdir(DOWNLOADS_DIR);
  } catch (err) {
    throw new Error(
      `Impossible de lire le dossier Downloads (${DOWNLOADS_DIR}) : ${err.message}`
    );
  }
  if (!entries.includes(filename)) {
    throw new Error(
      `Aucun fichier nommé exactement "${filename}" trouvé dans ${DOWNLOADS_DIR}.`
    );
  }

  let stats;
  try {
    stats = await fs.stat(sourcePath);
  } catch (err) {
    throw new Error(`Impossible d'accéder à "${sourcePath}" : ${err.message}`);
  }
  if (!stats.isFile()) {
    throw new Error(`"${filename}" n'est pas un fichier régulier.`);
  }

  // --- Vérifie la fraîcheur du fichier (dernière écriture < 5 min) ---
  const ageMs = Date.now() - stats.mtimeMs;
  if (ageMs > MAX_AGE_MS) {
    const ageMin = (ageMs / 60000).toFixed(1);
    throw new Error(
      `"${filename}" a été modifié il y a ${ageMin} min (> 5 min). ` +
        `Déplacement refusé pour éviter de récupérer un fichier obsolète.`
    );
  }
  if (ageMs < 0) {
    throw new Error(
      `Horodatage de "${filename}" incohérent (dans le futur). Déplacement refusé.`
    );
  }

  // --- Prépare la destination ---
  await fs.mkdir(destination, { recursive: true });
  const targetPath = path.join(destination, filename);

  try {
    await fs.access(targetPath);
    throw new Error(
      `Un fichier "${filename}" existe déjà dans "${destination}". Déplacement refusé (pas d'écrasement).`
    );
  } catch (err) {
    if (err.code !== "ENOENT") throw err; // relance si ce n'est pas "n'existe pas"
  }

  // --- Déplace le fichier (gère le cas cross-device) ---
  try {
    await fs.rename(sourcePath, targetPath);
  } catch (err) {
    if (err.code === "EXDEV") {
      await fs.copyFile(sourcePath, targetPath);
      await fs.unlink(sourcePath);
    } else {
      throw new Error(`Échec du déplacement : ${err.message}`);
    }
  }

  const ageSec = (ageMs / 1000).toFixed(1);
  return {
    content: [
      {
        type: "text",
        text:
          `Fichier déplacé avec succès.\n` +
          `Source      : ${sourcePath}\n` +
          `Destination : ${targetPath}\n` +
          `Âge du fichier au moment du déplacement : ${ageSec} s`,
      },
    ],
  };
}

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("mcp-move-download: serveur MCP démarré (stdio).");
}

main().catch((err) => {
  console.error("Erreur fatale du serveur MCP :", err);
  process.exit(1);
});
