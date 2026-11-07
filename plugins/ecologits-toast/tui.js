import { Plugin } from "@opencode/plugin/tui";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const LAST_URL = "http://127.0.0.1:8787/ecologits/last";

// Rough average for a global passenger car: ~200 g CO₂ per kilometre.
const KG_CO2_PER_KM = 0.2;

const STORE_VERSION = 1;

// Allow per-install configuration without hardcoding paths in the source.
// The plugin loads a .env file from its own directory; ECOLOGITS_SESSIONS_PATH
// can be set there (or exported to OpenCode's launch environment).
const PLUGIN_DIR = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_ENV_PATH = path.join(PLUGIN_DIR, ".env");

function loadEnvFile(envPath) {
  try {
    if (!fs.existsSync(envPath)) return;
    const raw = fs.readFileSync(envPath, "utf-8");
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      let value = trimmed.slice(eq + 1).trim();
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      if (process.env[key] === undefined) {
        process.env[key] = value;
      }
    }
  } catch (err) {
    console.error(`[ecologits-toast] failed to load ${envPath}:`, err);
  }
}

loadEnvFile(PLUGIN_ENV_PATH);

const SESSIONS_PATH = process.env.ECOLOGITS_SESSIONS_PATH
  ? path.resolve(process.env.ECOLOGITS_SESSIONS_PATH)
  : path.join(os.homedir(), ".local", "share", "opencode", "ecologits-sessions.json");

function normalizeValue(value) {
  if (typeof value === "number") {
    return { min: value, max: value };
  }
  if (value && typeof value === "object") {
    return { min: value.min ?? 0, max: value.max ?? 0 };
  }
  return { min: 0, max: 0 };
}

function fmt(num) {
  if (num === 0) return "0";
  const digits = num < 0.01 ? 3 : 2;
  const rounded = Number(num.toFixed(digits));
  if (rounded >= 1000) {
    return rounded.toLocaleString(undefined, { maximumFractionDigits: digits });
  }
  return rounded.toString();
}

function formatMassRange(minKg, maxKg) {
  const pick = (kg) => {
    if (kg < 1e-6) return { value: kg * 1e9, unit: "µg" };
    if (kg < 1e-3) return { value: kg * 1e6, unit: "mg" };
    if (kg < 1) return { value: kg * 1e3, unit: "g" };
    return { value: kg, unit: "kg" };
  };

  const a = pick(minKg);
  const b = pick(maxKg);

  if (a.unit === b.unit) {
    if (Math.abs(a.value - b.value) < Number.EPSILON) {
      return `${fmt(a.value)} ${a.unit} CO₂`;
    }
    return `${fmt(a.value)}–${fmt(b.value)} ${a.unit} CO₂`;
  }
  return `${fmt(a.value)} ${a.unit}–${fmt(b.value)} ${b.unit} CO₂`;
}

function formatEnergyRange(minKwh, maxKwh) {
  const pick = (kwh) => {
    if (kwh < 1e-3) return { value: kwh * 1e6, unit: "mWh" };
    if (kwh < 1) return { value: kwh * 1e3, unit: "Wh" };
    return { value: kwh, unit: "kWh" };
  };

  const a = pick(minKwh);
  const b = pick(maxKwh);

  if (a.unit === b.unit) {
    if (Math.abs(a.value - b.value) < Number.EPSILON) {
      return `${fmt(a.value)} ${a.unit}`;
    }
    return `${fmt(a.value)}–${fmt(b.value)} ${a.unit}`;
  }
  return `${fmt(a.value)} ${a.unit}–${fmt(b.value)} ${b.unit}`;
}

function formatDistance(kgCo2) {
  const km = kgCo2 / KG_CO2_PER_KM;
  if (km >= 1) {
    return `${km.toFixed(2)} km`;
  }
  if (km >= 0.001) {
    return `${(km * 1000).toFixed(1)} m`;
  }
  return `${(km * 1_000_000).toFixed(1)} mm`;
}

function formatDistanceRange(minKg, maxKg) {
  const a = formatDistance(minKg);
  const b = formatDistance(maxKg);
  if (a === b) return a;
  return `${a}–${b}`;
}

function emptyImpact() {
  return { gwp: { min: 0, max: 0 }, energy: { min: 0, max: 0 } };
}

function ensureDir(filePath) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function readStore() {
  try {
    if (!fs.existsSync(SESSIONS_PATH)) return null;
    const raw = fs.readFileSync(SESSIONS_PATH, "utf-8");
    const parsed = JSON.parse(raw);
    if (parsed?.version === STORE_VERSION) return parsed;
  } catch (err) {
    console.error("[ecologits-toast] failed to read sessions store:", err);
  }
  return null;
}

function writeStore(lifetime, sessions) {
  try {
    ensureDir(SESSIONS_PATH);
    const tmp = `${SESSIONS_PATH}.tmp`;
    const payload = {
      version: STORE_VERSION,
      lifetime,
      sessions: Object.fromEntries(sessions),
      writtenAt: new Date().toISOString(),
    };
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), "utf-8");
    fs.renameSync(tmp, SESSIONS_PATH);
  } catch (err) {
    console.error("[ecologits-toast] failed to write sessions store:", err);
  }
}

function addImpact(target, gwp, energy) {
  target.gwp.min += gwp.min;
  target.gwp.max += gwp.max;
  target.energy.min += energy.min;
  target.energy.max += energy.max;
}

export default Plugin.define({
  id: "ecologits-toast",
  setup(context) {
    let lastTime = null;

    const stored = readStore();
    const lifetimeKgCo2 = stored?.lifetime ?? emptyImpact();
    const sessionTotals = new Map();

    if (stored?.sessions) {
      for (const [id, data] of Object.entries(stored.sessions)) {
        sessionTotals.set(id, {
          ...emptyImpact(),
          requestCount: data.requestCount ?? 0,
          createdAt: data.createdAt,
          updatedAt: data.updatedAt,
          closedAt: data.closedAt ?? null,
        });
        addImpact(sessionTotals.get(id), normalizeValue(data.gwp), normalizeValue(data.energy));
      }
    }

    function getOrCreateSession(sessionID) {
      if (!sessionTotals.has(sessionID)) {
        const now = new Date().toISOString();
        sessionTotals.set(sessionID, {
          ...emptyImpact(),
          requestCount: 0,
          createdAt: now,
          updatedAt: now,
          closedAt: null,
        });
      }
      return sessionTotals.get(sessionID);
    }

    function persist() {
      writeStore(lifetimeKgCo2, sessionTotals);
    }

    context.ui.toast.show({
      title: "EcoLogits",
      message: "Plugin loaded",
      variant: "success",
      duration: 3000,
    });

    const stop = context.data.listen(async (event) => {
      try {
        const details = event?.details ?? event;
        const data = details?.data ?? {};

        if (details?.type === "session.created") {
          if (!data.parentID && data.sessionID) {
            getOrCreateSession(data.sessionID);
            persist();
          }
          return;
        }

        if (details?.type === "session.deleted") {
          if (data.sessionID) {
            const session = sessionTotals.get(data.sessionID);
            if (session) {
              session.closedAt = new Date().toISOString();
              persist();
            }
          }
          return;
        }

        if (details?.type !== "session.execution.succeeded") {
          return;
        }

        // Give the proxy a moment to write the estimate file.
        await new Promise((resolve) => setTimeout(resolve, 500));

        let estimate;
        try {
          const response = await fetch(LAST_URL);
          if (!response.ok) {
            return;
          }
          estimate = await response.json();
        } catch {
          return;
        }

        if (!estimate?.impacts || estimate.time === lastTime) {
          return;
        }

        // Ignore stale estimates from previous sessions.
        if (Date.now() / 1000 - estimate.time > 30) {
          return;
        }

        lastTime = estimate.time;

        const gwp = normalizeValue(estimate.impacts.gwp?.value);
        const energy = normalizeValue(estimate.impacts.energy?.value);
        const sessionID = data.sessionID;

        addImpact(lifetimeKgCo2, gwp, energy);

        let session;
        if (sessionID) {
          session = getOrCreateSession(sessionID);
          addImpact(session, gwp, energy);
          session.requestCount += 1;
          session.updatedAt = new Date().toISOString();
        }

        persist();

        const co2Text = formatMassRange(gwp.min, gwp.max);
        const energyText = formatEnergyRange(energy.min, energy.max);
        const driveThis = formatDistanceRange(gwp.min, gwp.max);
        const driveLifetime = formatDistanceRange(lifetimeKgCo2.gwp.min, lifetimeKgCo2.gwp.max);
        const driveSession = session
          ? formatDistanceRange(session.gwp.min, session.gwp.max)
          : driveLifetime;

        context.ui.toast.show({
          title: "EcoLogits estimate",
          message:
            `${co2Text} · ${energyText}\n` +
            `≈ driving a car ${driveThis}\n` +
            `session ≈ ${driveSession} · lifetime ≈ ${driveLifetime}`,
          variant: "info",
          duration: 10000,
        });
      } catch (err) {
        context.ui.toast.show({
          title: "EcoLogits plugin error",
          message: String(err?.message ?? err),
          variant: "error",
          duration: 5000,
        });
      }
    });

    return stop;
  },
});
