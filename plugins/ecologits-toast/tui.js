import { Plugin } from "@opencode/plugin/tui";

const LAST_URL = "http://127.0.0.1:8787/ecologits/last";

// Rough average for a global passenger car: ~200 g CO₂ per kilometre.
const KG_CO2_PER_KM = 0.2;

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

export default Plugin.define({
  id: "ecologits-toast",
  setup(context) {
    let lastTime = null;
    let sessionKgCo2Max = 0;

    context.ui.toast.show({
      title: "EcoLogits",
      message: "Plugin loaded",
      variant: "success",
      duration: 3000,
    });

    const stop = context.data.listen(async (event) => {
      try {
        const details = event?.details ?? event;
        if (details?.type !== "session.execution.succeeded") {
          return;
        }

        // Give the proxy a moment to write the estimate file.
        await new Promise((resolve) => setTimeout(resolve, 500));

        let data;
        try {
          const response = await fetch(LAST_URL);
          if (!response.ok) {
            return;
          }
          data = await response.json();
        } catch {
          return;
        }

        if (!data?.impacts || data.time === lastTime) {
          return;
        }

        // Ignore stale estimates from previous sessions.
        if (Date.now() / 1000 - data.time > 30) {
          return;
        }

        lastTime = data.time;

        const gwp = normalizeValue(data.impacts.gwp?.value);
        const energy = normalizeValue(data.impacts.energy?.value);
        sessionKgCo2Max += gwp.max;

        const co2Text = formatMassRange(gwp.min, gwp.max);
        const energyText = formatEnergyRange(energy.min, energy.max);
        const driveThis = `${formatDistance(gwp.min)}–${formatDistance(gwp.max)}`;
        const driveSession = formatDistance(sessionKgCo2Max);

        context.ui.toast.show({
          title: "EcoLogits estimate",
          message:
            `${co2Text} · ${energyText}\n` +
            `≈ driving a car ${driveThis} (session total ≈ ${driveSession})`,
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
