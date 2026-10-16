// Card styles on HA theme tokens, sized like tile cards and their features (56 px header, 42 px controls).

import { css } from "lit";

export const cardStyles = css`
  :host {
    display: block;
    height: 100%;
    --ml-fill: var(--ha-color-fill-neutral-quiet-resting, rgba(var(--rgb-primary-text-color), 0.05));
    --ml-fill-hover: var(--ha-color-fill-neutral-quiet-hover, rgba(var(--rgb-primary-text-color), 0.1));
    --ml-radius: var(--ha-card-features-border-radius, var(--ha-border-radius-lg, 12px));
    --ml-focus: var(--ha-color-focus, var(--primary-color));
  }
  ha-card {
    display: flex;
    flex-direction: column;
    height: 100%;
    box-sizing: border-box;
    overflow: hidden;
    container-type: inline-size;
  }
  button {
    margin: 0;
    padding: 0;
    border: none;
    background: none;
    color: inherit;
    font: inherit;
    text-align: start;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  button:disabled {
    cursor: default;
    opacity: 0.45;
  }
  button:focus-visible {
    outline: 2px solid var(--ml-focus);
    outline-offset: -2px;
  }

  /* header */
  .header {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: none;
    min-height: 56px;
    padding: 0 10px;
    border-radius: var(--ha-card-border-radius, var(--ha-border-radius-lg, 12px));
  }
  .icon {
    --tint: var(--state-inactive-color, var(--disabled-text-color));
    position: relative;
    flex: none;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border-radius: var(--ha-border-radius-pill, 9999px);
    overflow: hidden;
    color: var(--tint);
    --mdc-icon-size: 24px;
    transition: color 180ms ease-in-out;
  }
  .icon.on {
    --tint: var(--state-light-active-color, var(--amber-color, #ffc107));
  }
  .icon::before {
    content: "";
    position: absolute;
    inset: 0;
    background: var(--tint);
    opacity: 0.2;
    transition: background-color 180ms ease-in-out;
  }
  .info {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .name,
  .status {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .name {
    font-size: var(--ha-font-size-m, 14px);
    font-weight: var(--ha-font-weight-medium, 500);
    line-height: var(--ha-line-height-normal, 1.6);
    letter-spacing: 0.1px;
    color: var(--primary-text-color);
  }
  .status {
    font-size: var(--ha-font-size-s, 12px);
    line-height: var(--ha-line-height-condensed, 1.2);
    letter-spacing: 0.4px;
    color: var(--secondary-text-color);
  }

  .body {
    display: flex;
    flex-direction: column;
    gap: 12px;
    flex: 1;
    min-height: 0;
    padding: 0 12px 12px;
  }

  /* manual lights */
  .manual {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: none;
    min-height: 42px;
    padding: 0 5px 0 12px;
    border-radius: var(--ml-radius);
    color: var(--ha-color-on-warning-normal, var(--warning-color));
    background: var(
      --ha-color-fill-warning-quiet-resting,
      color-mix(in srgb, var(--warning-color) 15%, transparent)
    );
    font-size: var(--ha-font-size-m, 14px);
    --mdc-icon-size: 20px;
  }
  .manual-text {
    flex: 1;
    min-width: 0;
    white-space: nowrap;
  }
  .reclaim {
    flex: none;
    height: 32px;
    padding: 0 14px;
    border-radius: var(--ha-border-radius-md, 8px);
    font-weight: var(--ha-font-weight-medium, 500);
    background: var(
      --ha-color-fill-warning-normal-resting,
      color-mix(in srgb, var(--warning-color) 25%, transparent)
    );
  }
  .reclaim:hover {
    background: var(
      --ha-color-fill-warning-normal-hover,
      color-mix(in srgb, var(--warning-color) 35%, transparent)
    );
  }

  /* At most --columns tiles per row, fewer where they would get too narrow for a name;
     with a fixed row count from the layout editor the grid scrolls. */
  .scenes {
    display: grid;
    grid-template-columns: repeat(
      auto-fill,
      minmax(max(96px, (100% - (var(--columns) - 1) * 8px) / var(--columns)), 1fr)
    );
    gap: 8px;
    align-content: start;
    min-height: 0;
    overflow-y: auto;
  }
  .scene {
    position: relative;
    border-radius: var(--ml-radius);
    background: var(--ml-fill);
    transition:
      background-color 180ms ease-in-out,
      box-shadow 180ms ease-in-out;
  }
  .scene:hover {
    background: var(--ml-fill-hover);
  }
  .scene.active {
    background: var(--ha-color-fill-primary-quiet-resting, rgba(var(--rgb-primary-color), 0.1));
    box-shadow: inset 0 0 0 2px var(--primary-color);
  }
  .scene-main {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 100%;
    height: 100%;
    box-sizing: border-box;
    padding: 8px 8px 10px;
    border-radius: inherit;
  }
  .swatch {
    height: 10px;
    border-radius: var(--ha-border-radius-pill, 9999px);
    background: var(--swatch);
    box-shadow: inset 0 0 0 1px var(--divider-color);
  }
  .swatch.moving {
    background-size: 300% 100%;
    animation: ml-drift 12s ease-in-out infinite alternate;
  }
  @keyframes ml-drift {
    from {
      background-position: 0% 50%;
    }
    to {
      background-position: 100% 50%;
    }
  }
  .scene-name {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
    overflow-wrap: break-word;
    hyphens: auto;
    font-size: var(--ha-font-size-m, 14px);
    font-weight: var(--ha-font-weight-medium, 500);
    line-height: var(--ha-line-height-condensed, 1.2);
    color: var(--primary-text-color);
  }
  .scene.playable .scene-name {
    padding-inline-end: 24px;
  }
  .play {
    position: absolute;
    inset-inline-end: 2px;
    bottom: 2px;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border-radius: var(--ha-border-radius-pill, 9999px);
    color: var(--secondary-text-color);
    --mdc-icon-size: 20px;
  }
  .play:hover {
    color: var(--primary-text-color);
    background: var(--ml-fill-hover);
  }
  .scene.active .play {
    color: var(--primary-color);
  }

  /* lights */
  .lights {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .light {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 100%;
    height: 32px;
    padding: 0 12px 0 8px;
    box-sizing: border-box;
    border-radius: var(--ha-border-radius-pill, 9999px);
    background: var(--ml-fill);
    font-size: var(--ha-font-size-s, 12px);
    --mdc-icon-size: 18px;
  }
  .light:hover {
    background: var(--ml-fill-hover);
  }
  .light.on ha-state-icon {
    color: var(--state-light-active-color, var(--amber-color, #ffc107));
  }
  .light-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .light-status {
    flex: none;
    color: var(--secondary-text-color);
  }
  .light.is-manual .light-status {
    color: var(--ha-color-on-warning-normal, var(--warning-color));
  }
  .light.is-unavailable {
    color: var(--disabled-text-color);
  }

  /* controls, like tile card features */
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    flex: none;
  }
  .control,
  .brightness {
    display: flex;
    align-items: center;
    height: 42px;
    border-radius: var(--ml-radius);
    background: var(--ml-fill);
    font-weight: var(--ha-font-weight-medium, 500);
    font-size: var(--ha-font-size-m, 14px);
    --mdc-icon-size: 20px;
  }
  .control {
    flex: 1 1 auto;
    justify-content: center;
    gap: 6px;
    min-width: 42px;
    padding: 0 12px;
    box-sizing: border-box;
  }
  .control:not(:disabled):hover,
  .step:not(:disabled):hover {
    background: var(--ml-fill-hover);
  }
  .brightness {
    flex: 1 1 auto;
    justify-content: space-between;
  }
  .step {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 42px;
    height: 42px;
    border-radius: var(--ml-radius);
  }
  .value {
    min-width: 48px;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }
  @container (max-width: 320px) {
    .control .label {
      display: none;
    }
  }
  @container (max-width: 260px) {
    .manual ha-icon,
    .manual .wide {
      display: none;
    }
  }

  .warning {
    padding: 12px 16px;
    color: var(--ha-color-on-warning-normal, var(--warning-color));
    background: var(
      --ha-color-fill-warning-quiet-resting,
      color-mix(in srgb, var(--warning-color) 15%, transparent)
    );
  }
  .warning.missing {
    padding: 8px 12px;
    border-radius: var(--ml-radius);
    font-size: var(--ha-font-size-s, 12px);
  }

  @media (prefers-reduced-motion: reduce) {
    .swatch.moving {
      animation: none;
    }
  }
`;
