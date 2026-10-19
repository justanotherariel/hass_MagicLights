// Events the editor's cards send up to ml-scene-editor.

import type { Part, StoredScene } from "../../shared/types";

/** "ml-scene-edit": the scene after one edit, and the part it now plays that is new. */
export interface SceneEdit {
  scene: StoredScene;
  /** The layer to select afterwards. */
  select?: number;
  added?: Part;
}

/** "ml-part-edit": the selected layer's part after one edit, for every scene that uses it. */
export interface PartEdit {
  part: Part;
}

/** "ml-part-select" */
export interface PartSelect {
  index: number;
}

/** "ml-time": the preview time, in seconds since its start. */
export interface TimeSelect {
  t: number;
}
