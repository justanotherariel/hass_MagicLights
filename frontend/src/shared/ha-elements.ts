// Waits for the HA elements the panel renders; HA defines most of them lazily, as their
// users load. Triggers those loads (the lovelace panel, the light and lock more-info
// controls, a tile card, the automation editor) and resolves with the tags still missing.

export const PANEL_TAGS = [
  "ha-top-app-bar-fixed",
  "ha-menu-button",
  "ha-card",
  "ha-button",
  "ha-icon-button",
  "ha-icon-button-arrow-prev",
  "ha-icon",
  "ha-svg-icon",
  "ha-tab-group",
  "ha-tab-group-tab",
  "ha-assist-chip",
  "ha-switch",
  "ha-select",
  "ha-selector",
  "ha-dialog",
  "ha-adaptive-dialog",
  "ha-dialog-footer",
  "ha-alert",
  "ha-list-base",
  "ha-list-item-base",
  "ha-list-item-button",
  "ha-sortable",
  "ha-dropdown",
  "ha-dropdown-item",
  "ha-spinner",
  "ha-input",
  "ha-expansion-panel",
  "ha-icon-picker",
  "ha-slider",
  "ha-control-select",
  "ha-control-slider",
  "ha-control-button",
  "ha-control-button-group",
  "ha-control-switch",
  "ha-filter-chip",
  "ha-chip-set",
  "ha-badge",
  "ha-bottom-sheet",
  "ha-tooltip",
  "ha-tile-container",
  "ha-tile-icon",
  "ha-tile-info",
  "ha-tile-badge",
  "ha-settings-row",
] as const;

const PRELOAD_WAIT = 1500;
const STEP_TIMEOUT = 8000;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const withTimeout = (promise: Promise<unknown>, ms: number) =>
  Promise.race([promise.catch(() => undefined), sleep(ms)]);

interface RouteLoader {
  routes?: Record<string, { load?: () => Promise<unknown> }>;
}

/** Imports one of HA's lazily loaded panel modules; `_getRoutes` is side-effect free. */
const loadPanelModule = async (component: string): Promise<void> => {
  await customElements.whenDefined("partial-panel-resolver");
  const resolver = document.createElement("partial-panel-resolver") as HTMLElement & {
    _getRoutes?: (panels: { component_name: string; url_path: string }[]) => RouteLoader;
  };
  const routes = resolver._getRoutes?.([{ component_name: component, url_path: "__ml" }]);
  await routes?.routes?.__ml?.load?.();
};

interface CardHelpers {
  importMoreInfoControl?: (type: string) => Promise<unknown>;
  createCardElement?: (config: Record<string, unknown>) => HTMLElement;
}

/**
 * The control-generation elements (ha-control-slider, -button, -switch, -button-group)
 * come with the more-info controls of lights and locks, the ha-tile-* parts with the
 * tile card. Lovelace's card helpers load both; the lovelace module defines them.
 */
const loadCardParts = async (): Promise<void> => {
  await loadPanelModule("lovelace");
  const helpers = await (window as { loadCardHelpers?: () => Promise<CardHelpers> }).loadCardHelpers?.();
  if (!helpers) return;
  try {
    helpers.createCardElement?.({ type: "tile", entity: "sun.sun" });
  } catch {
    // Only the import it starts matters.
  }
  await Promise.all(["light", "lock"].map((type) => helpers.importMoreInfoControl?.(type)));
};

const loadConfigRoute = async (route: string): Promise<void> => {
  await loadPanelModule("config");
  await customElements.whenDefined("ha-panel-config");
  const config = document.createElement("ha-panel-config") as HTMLElement & {
    routerOptions?: RouteLoader;
  };
  await config.routerOptions?.routes?.[route]?.load?.();
};

const load = async (tags: readonly string[]): Promise<string[]> => {
  const missing = () => tags.filter((tag) => !customElements.get(tag));
  if (missing().length) {
    const defined = Promise.all(missing().map((tag) => customElements.whenDefined(tag)));
    // ha-tab-group only comes with the lovelace module; many others with HA's more-info
    // preload (~300 elements) right after start.
    await withTimeout(loadCardParts(), STEP_TIMEOUT);
    await withTimeout(defined, PRELOAD_WAIT);
  }
  // The automation editor brings forms, pickers and dialogs.
  if (missing().length) await withTimeout(loadConfigRoute("automation"), STEP_TIMEOUT);
  const still = missing();
  if (still.length) console.warn("[magic-lights] HA elements unavailable:", still);
  return still;
};

const pending = new Map<string, Promise<string[]>>();

export const ensureHaElements = (tags: readonly string[] = PANEL_TAGS): Promise<string[]> => {
  const key = tags.join(",");
  let result = pending.get(key);
  if (!result) {
    result = load(tags);
    pending.set(key, result);
  }
  return result;
};
