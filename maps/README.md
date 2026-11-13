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

## arena.wad: OpenArena's "The Other Side" (oacmpdm5), as a Doom II arena

- **Source:** `oacmpdm5.map` by Jan Groothuijse, from the OpenArena Community Mappack Volume 1
  (github.com/NeonKnightOA/oacmpvol1), released under the **GNU GPL, version 2 or later**. The map
  source and its readme are kept in [`src/`](src). Only the layout and item placement are used: no
  OpenArena textures, sounds or models are included.
- **Converted by** `tools/quake2doom/quake2doom.py`, which also reads Quake III `.map` files, then
  nodes built with `tools/quake2doom/nodes.py`.
- **Looks and gameplay** come from Freedoom: Phase 2 (BSD): Doom II textures, weapons and monsters.
- Licence of this file: GPL-2.0-or-later, as a derivative of the GPL map source.

What changes from OpenArena:
- Curved patches are flattened into small straight blocks.
- Doom can't stack floors. Where walkways stack, the lower one is kept and the space above it is
  opened up, so what is left of the upper walkway is a raised ledge looking down on it. The bridges
  over the pit are gone; the block in the middle keeps its top.
- Jump pads and teleporters are Doom teleporters. A jump pad sets you down where the jump would have
  landed (if that floor was kept); a teleporter whose landing was an upper walkway now lands on the
  floor underneath.
- It is a single-player fight, not a deathmatch: chaingunners, revenants, mancubi, arachnotrons and
  a few arch-viles (17 / 27 / 39 monsters on easy / medium / hard). They stand out of sight of the
  start, facing into the arena, so nothing reacts until the first shot is heard. A super shotgun, shells and a backpack are at the start; 20 boxes of shells and
  10 boxes of rockets are spread around.
- The level sits in the MAP07 slot, so Doom II's own rule applies: when the last mancubus dies the
  exit pad at the north end of the arena lowers. Step on it to finish; the game carries on into
  Freedoom's level 8.

Rebuild:
    python3 tools/quake2doom/quake2doom.py maps/src/oacmpdm5.map tools/quake2doom/freedoom2_textures.json arena MAP07
    python3 tools/quake2doom/nodes.py arena.raw.wad maps/arena.wad

Options for small test builds: `--crop x0,y0,x1,y1` keeps one area, `--monsters N` places only N monsters.
