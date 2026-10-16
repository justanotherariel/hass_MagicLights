// Panel styles built on HA theme tokens only.

import { css } from "lit";

export const pageStyles = css`
  :host {
    display: block;
  }
  .section-title {
    margin: var(--ha-space-6) var(--ha-space-1) var(--ha-space-3);
    font-size: var(--ha-font-size-l);
    font-weight: var(--ha-font-weight-medium);
    line-height: var(--ha-line-height-condensed);
    color: var(--primary-text-color);
  }
  .section-title:first-child {
    margin-top: var(--ha-space-2);
  }
  .grid {
    display: grid;
    gap: var(--ha-space-4);
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
  }
  .secondary {
    color: var(--secondary-text-color);
  }
  ha-card {
    display: block;
  }
  /* The text gets the width a switch or a short field does not need. */
  ha-settings-row {
    --settings-row-prefix-flex: 3;
  }
  /* Narrow rows draw a divider above themselves; the card edge is one already, and
     rows kept wide (switches, short fields) get the same divider. */
  ha-card > ha-settings-row:first-child {
    border-top: none;
  }
  :host([narrow]) ha-card > ha-settings-row + ha-settings-row {
    border-top: 1px solid var(--divider-color);
  }
  ha-alert {
    display: block;
    margin-bottom: var(--ha-space-4);
  }
`;

/** Swatch strip, round target icon and the reset button wrapping a tile's clickable area. */
export const tileStyles = css`
  .strip {
    height: 6px;
    border-radius: var(--ha-border-radius-pill);
    background: var(--strip, var(--ha-color-fill-neutral-normal-resting, var(--divider-color)));
  }
  .strip.moving {
    background-size: 300% 100%;
    animation: ml-drift 16s ease-in-out infinite alternate;
  }
  @keyframes ml-drift {
    from {
      background-position: 0% 50%;
    }
    to {
      background-position: 100% 50%;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .strip.moving {
      animation: none;
    }
  }
  .target-icon {
    flex: none;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    border-radius: var(--ha-border-radius-pill);
    color: var(--state-inactive-color, var(--disabled-text-color));
    background-color: var(--ha-color-fill-neutral-quiet-resting, var(--secondary-background-color));
    --mdc-icon-size: 24px;
    transition:
      color var(--ha-animation-duration-normal) ease,
      background-color var(--ha-animation-duration-normal) ease;
  }
  .target-icon.on {
    color: var(--state-light-active-color, var(--amber-color));
    background-color: var(--ha-color-fill-warning-quiet-resting, var(--secondary-background-color));
  }
  button.plain {
    display: flex;
    align-items: center;
    gap: var(--ha-space-3);
    min-width: 0;
    margin: 0;
    padding: 0;
    border: none;
    background: none;
    color: inherit;
    font: inherit;
    text-align: start;
    cursor: pointer;
    border-radius: var(--ha-border-radius-md);
  }
  button.plain:focus-visible {
    outline: 2px solid var(--ha-color-focus, var(--primary-color));
    outline-offset: 2px;
  }
  .name {
    font-size: var(--ha-font-size-l);
    font-weight: var(--ha-font-weight-medium);
    line-height: var(--ha-line-height-condensed);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .status {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    overflow: hidden;
    font-size: var(--ha-font-size-m);
    color: var(--secondary-text-color);
  }
`;

/** Round swatch of a scene; the ring keeps near-white swatches visible on light cards. */
export const dotStyles = css`
  .dot {
    display: inline-block;
    flex: none;
    width: 20px;
    height: 20px;
    border-radius: var(--ha-border-radius-circle);
    box-shadow: inset 0 0 0 1px var(--divider-color);
  }
`;

/**
 * Filled assist chips. HA's own active style sits under the fill, so active chips recolour
 * the fill; the label keeps the text colour for contrast.
 */
export const chipStyles = css`
  ha-assist-chip {
    --ha-assist-chip-filled-container-color: var(
      --ha-color-fill-neutral-quiet-resting,
      rgba(var(--rgb-primary-text-color), 0.06)
    );
  }
  ha-assist-chip.active {
    --ha-assist-chip-filled-container-color: var(
      --ha-color-fill-primary-normal-resting,
      rgba(var(--rgb-primary-color), 0.18)
    );
    --md-assist-chip-label-text-weight: var(--ha-font-weight-medium);
  }
`;

/** Dashed card that creates something ("New scene", "New zone"). */
export const newCardStyles = css`
  .new {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--ha-space-1);
    padding: var(--ha-space-4);
    box-sizing: border-box;
    border: 1px dashed var(--divider-color);
    border-radius: var(--ha-card-border-radius, var(--ha-border-radius-lg));
    background: none;
    color: var(--secondary-text-color);
    font: inherit;
    text-align: center;
    cursor: pointer;
  }
  .new:hover {
    color: var(--primary-color);
    border-color: var(--primary-color);
  }
  .new:focus-visible {
    outline: 2px solid var(--ha-color-focus, var(--primary-color));
  }
`;
