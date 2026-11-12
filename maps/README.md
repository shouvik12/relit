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

## quake-e1.wad: Quake episode 1, E1M1 to E1M4, as Doom levels

The Slipgate Complex, Castle of the Damned, The Necropolis and The Grisly Grotto, from the
same GPL map source release, converted the same way. Finishing one level leads into the
next; after the Grisly Grotto the game carries on into the player's own E1M5.

E1M3 and E1M4 are big: after conversion they have more than 32767 sidedefs, which vanilla
Doom can't index. Relit's engine reads map indices as unsigned 16-bit numbers (as Boom-style
ports do), so it handles up to 65535. The converter also merges collinear wall pieces first.

Rebuild (the converter needs `pip install numpy shapely`):
    for m in 1 2 3 4; do
      python3 tools/quake2doom/quake2doom.py e1m$m.map tools/quake2doom/freedoom1_textures.json e1m$m E1M$m
      zdbsp -R -o e1m$m.wad e1m$m.raw.wad
    done
    python3 tools/quake2doom/pack.py maps/quake-e1.wad e1m1.wad e1m2.wad e1m3.wad e1m4.wad
