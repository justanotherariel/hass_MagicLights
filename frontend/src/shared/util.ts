// Small DOM and formatting helpers shared by the panel, the editor and the card.

/** Guarded define: a cache-busted module URL may load the bundle twice. */
export const defineElement = (tag: string, cls: CustomElementConstructor): void => {
  if (!customElements.get(tag)) customElements.define(tag, cls);
};

/** Fire a bubbling, shadow-crossing event (HA's fireEvent). */
export const fire = <T>(el: EventTarget, type: string, detail?: T): void => {
  el.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
};

/** Navigate inside HA: HA's router listens for "location-changed" on window. */
export const navigate = (
  path: string,
  options: { replace?: boolean; data?: unknown } = {},
): void => {
  const data = options.data ?? null;
  if (options.replace) {
    history.replaceState(history.state?.root ? { root: true } : data, "", path);
  } else {
    history.pushState(data, "", path);
  }
  fire(window, "location-changed", { replace: !!options.replace });
};

export const showMoreInfo = (el: HTMLElement, entityId: string): void =>
  fire(el, "hass-more-info", { entityId });

export const showToast = (el: HTMLElement, message: string, duration = 3000): void =>
  fire(el, "hass-notification", { message, duration });

export interface ConfirmParams {
  title: string;
  text: string;
  confirmText: string;
  destructive?: boolean;
}

export interface ConfirmRequest extends ConfirmParams {
  resolve: (confirmed: boolean) => void;
}

/** Answered by the panel root, which renders one confirmation dialog. */
export const confirmAction = (el: HTMLElement, params: ConfirmParams): Promise<boolean> =>
  new Promise((resolve) => fire<ConfirmRequest>(el, "ml-confirm", { ...params, resolve }));

/** Asks before deleting a room's scene; its parts stay for the other scenes. */
export const confirmRemoveScene = (el: HTMLElement, scene: { name: string }, room: string): Promise<boolean> =>
  confirmAction(el, {
    title: `Delete ${scene.name}?`,
    text: `The scene is removed from ${room}; its parts stay. If it is running, ${room} switches to Manual.`,
    confirmText: "Delete",
    destructive: true,
  });

export const debounce = <A extends unknown[]>(fn: (...args: A) => void, wait: number) => {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const debounced = (...args: A) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  debounced.cancel = () => clearTimeout(timer);
  return debounced;
};

/** "min_brightness" -> "Min brightness". */
export const humanise = (key: string): string => {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

const newId = (prefix: string): string => {
  const bytes = crypto.getRandomValues(new Uint8Array(4));
  return `${prefix}${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
};

/** Client-generated scene id, `s_` + 8 hex. */
export const newSceneId = (): string => newId("s_");

/** Client-generated id of your part, `p_` + 8 hex. */
export const newPartId = (): string => newId("p_");

/** HA's unit spacing: "60%" but "2700 K". */
export const withUnit = (value: number | string, unit: string): string =>
  unit === "%" ? `${value}%` : `${value} ${unit}`;

export const plural = (n: number, one: string, many = `${one}s`): string =>
  `${n} ${n === 1 ? one : many}`;

export const errorMessage = (err: unknown): string =>
  (err as { message?: string })?.message ?? String(err);

/** Runs an action and reports a failure as an HA toast; resolves to whether it succeeded. */
export const toastOnError = async (
  el: HTMLElement,
  action: Promise<unknown>,
  failure: string,
): Promise<boolean> => {
  try {
    await action;
    return true;
  } catch (err) {
    showToast(el, `${failure}: ${errorMessage(err)}`, 6000);
    return false;
  }
};

/**
 * Saves the new state of the switch that fired `ev`. A failed save flips it back, as its
 * bound value did not change and the next render would leave it as it is.
 */
export const saveSwitch = (
  el: HTMLElement,
  ev: Event,
  save: (checked: boolean) => Promise<unknown>,
  failure: string,
): void => {
  const input = ev.target as HTMLInputElement;
  const checked = input.checked;
  toastOnError(el, save(checked), failure).then((ok) => {
    if (!ok) input.checked = !checked;
  });
};
