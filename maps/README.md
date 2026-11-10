# Bundled levels

## slipgate.wad: Quake E1M1, "The Slipgate Complex", as a Doom level

- **Source:** the original Quake map source `e1m1.map`, released by John Romero under the
  **GNU GPL in October 2006** (via github.com/fzwoch/quake_map_source, which mirrors that release).
  Only the map geometry and entity placement are used. No Quake textures, sounds or models are included.
- **Converted by** `tools/quake2doom/quake2doom.py` (Relit, GPL-2.0-or-later), then nodes built with ZDBSP.
- **Looks and gameplay** come from Freedoom (BSD): Doom textures, monsters, weapons and items stand in for Quake's.
- Licence of this file: GPL-2.0-or-later, as a derivative of the GPL map source.

What changes from Quake: Doom levels can't stack floors, so where Quake has a room above another,
the floor on the walking route is kept. Slopes become small steps, water and slime become shallow
pools, Quake's elevator becomes a Doom lift, and the slime-pit teleporter secret is not included.
Grunts become a mix of Freedoom zombies, shotgun guys and imps; dogs become demons.

Rebuild:
    python3 tools/quake2doom/quake2doom.py e1m1.map tools/quake2doom/freedoom1_textures.json slipgate E1M1
    zdbsp -R -o maps/slipgate.wad slipgate.raw.wad
