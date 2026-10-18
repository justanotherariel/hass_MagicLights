// Events the editor's cards send up to ml-scene-editor.

import type { StoredScene } from "../../shared/types";

/** "ml-scene-edit": the scene after one edit. `follow`: room-only parts follow it (a look change). */
export interface SceneEdit {
  scene: StoredScene;
  follow?: boolean;
  /** The part to select afterwards. */
  select?: number;
}

/** "ml-part-select" */
export interface PartSelect {
  index: number;
}

/** "ml-room-select": the room to preview and try in. */
export interface RoomSelect {
  targetId: string;
}

/** "ml-time": the preview time, in seconds since its start. */
export interface TimeSelect {
  t: number;
}

export interface PartOption {
  index: number;
  label: string;
}
