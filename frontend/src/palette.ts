/**
 * Single source of truth for track and marker colours.
 *
 * The legend and the globe both read from here. Duplicating hex codes is how
 * the post-burn track ended up sharing the secondary's colour, which made the
 * two indistinguishable whenever a pair was selected.
 */
export const TRACK = {
  primary: "#d4a574",
  secondary: "#7eb8a8",
  baseline: "#e85d4c",
  maneuvered: "#5ee0b0",
  burn: "#f0d98c",
} as const;

export const OBJECT_TYPE = {
  station: "#d4a574",
  visual: "#7eb8a8",
  debris: "#e09b3d",
  active: "#9aa8b5",
} as const;
