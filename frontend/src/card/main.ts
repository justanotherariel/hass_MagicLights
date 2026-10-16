// Entry of magic-lights-card.js: the card and its card-picker entry.

import type { HomeAssistant } from "../shared/types";
import { defineElement } from "../shared/util";
import { MagicLightsCard } from "./magic-lights-card";
import { CARD_TYPE, isMagicSelect } from "./model";

interface CustomCardEntry {
  type: string;
  name: string;
  description: string;
  preview: boolean;
  documentationURL: string;
  getEntitySuggestion(hass: HomeAssistant, entityId: string): { config: Record<string, unknown> } | null;
}

const define = () => defineElement(CARD_TYPE, MagicLightsCard);
define();
// HA's app bundle installs a scoped custom element registry polyfill. This extra module can run
// before it, and the polyfilled registry HA's card loader asks does not see native definitions.
customElements.whenDefined("home-assistant").then(define);

const cards = ((window as { customCards?: CustomCardEntry[] }).customCards ??= []);

// A cache-busted module URL may load the bundle twice.
if (!cards.some((card) => card.type === CARD_TYPE)) {
  cards.push({
    type: CARD_TYPE,
    name: "Magic Lights",
    description: "Scenes of a room or zone: status, scene grid, manual lights and brightness.",
    preview: true,
    documentationURL: "https://github.com/justanotherariel/hass_MagicLights",
    getEntitySuggestion: (hass, entityId) =>
      isMagicSelect(hass, entityId) ? { config: { type: `custom:${CARD_TYPE}`, entity: entityId } } : null,
  });
}
