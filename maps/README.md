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

## quake-e1.wad: Quake episode 1, all eight levels, as Doom levels

The Slipgate Complex, Castle of the Damned, The Necropolis and The Grisly Grotto, from the
same GPL map source release, converted the same way. Finishing one level leads into the
next. The order follows Quake's own route, with the secret level in line:

| Doom slot | Quake level |
|---|---|
| E1M1 to E1M4 | e1m1 to e1m4 |
| E1M5 | e1m8, Ziggurat Vertigo (Quake's secret level, reached from e1m4) |
| E1M6 | e1m5, Gloom Keep (built with `--water-jump`) |
| E1M7 | e1m6, The Door to Chthon |
| E1M8 | e1m7, The House of Chthon (ends the episode; Chthon is a Cyberdemon) |

`--water-jump` raises a pool whose bank is up to 72 units above the water, so a Doom player
can step out where a Quake player would swim and climb. Gloom Keep's moat needs it.

E1M3 and E1M4 are big: after conversion they have more than 32767 sidedefs, which vanilla
Doom can't index. Relit's engine reads map indices as unsigned 16-bit numbers (as Boom-style
ports do), so it handles up to 65535. The converter also merges collinear wall pieces first.

Rebuild (the converter needs `pip install numpy shapely`):
    for m in 1 2 3 4; do
      python3 tools/quake2doom/quake2doom.py e1m$m.map tools/quake2doom/freedoom1_textures.json e1m$m E1M$m
      zdbsp -R -o e1m$m.wad e1m$m.raw.wad
    done
    python3 tools/quake2doom/quake2doom.py e1m8.map tools/quake2doom/freedoom1_textures.json e1m8 E1M5
    python3 tools/quake2doom/quake2doom.py e1m5.map tools/quake2doom/freedoom1_textures.json e1m5 E1M6 --water-jump
    python3 tools/quake2doom/quake2doom.py e1m6.map tools/quake2doom/freedoom1_textures.json e1m6 E1M7
    python3 tools/quake2doom/quake2doom.py e1m7.map tools/quake2doom/freedoom1_textures.json e1m7 E1M8
    for m in 8 5 6 7; do python3 tools/quake2doom/nodes.py e1m$m.raw.wad e1m$m.wad; done
    python3 tools/quake2doom/pack.py maps/quake-e1.wad e1m1.wad e1m2.wad e1m3.wad e1m4.wad e1m8.wad e1m5.wad e1m6.wad e1m7.wad

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

## wrecklight.wad: Stunt Rally 3's "Dark" track, as a Doom II car level

- **Source:** the `vlc1-dark` track from Stunt Rally 3 (github.com/stuntrally/tracks3) by Crystal
  Hammer and contributors, released under the **GNU GPL, version 3**. The heightmap, road spline,
  scene file and licence are kept in [`src/vlc1-dark/`](src/vlc1-dark). No Stunt Rally textures or
  sounds are included.
- **Converted by** `tools/stuntrally2doom/stuntrally2doom.py`, then nodes built with
  `tools/quake2doom/nodes.py`.
- **Looks and gameplay** come from Freedoom: Phase 2 (BSD): Doom II textures and monsters.
- Licence of this file: GPL-3.0-or-later, as a derivative of the GPL v3 track.

What changes from Stunt Rally:
- Doom floors are flat, so the track becomes a ribbon of small sectors along the road: the road, a
  kerb, two bands of roadside ground and a cliff rim on each side. Slopes become small steps.
- Bridges are raised road with a parapet over a drop. Ground below the track's water level is lava.
- Roadside sectors are tagged 900; the engine's car mode gives them less grip.
- The level sits in the MAP07 slot: when the last mancubus dies the gate before the finish line
  sinks. Crossing the line ends the level; for now the game carries on into Freedoom's level 8.

Rebuild (needs `pip install numpy shapely`):
    python3 tools/stuntrally2doom/stuntrally2doom.py maps/src/vlc1-dark wreck.raw.wad
    python3 tools/quake2doom/nodes.py wreck.raw.wad maps/wrecklight.wad

## cars/*.rcar: Stunt Rally 3 cars for Wrecklight

- **Ironhide** (`LK4.rcar`, by clarabox, import and interior by CryHam), **Quicksilver** (`XZ.rcar`,
  by Neubi) and **Old Thunder** (`OT.rcar`, by irokrhus), from Stunt Rally 3's `data/cars`. All
  **CC0 1.0**. Credits in `licenses/stuntrally-cars.txt`.
- **Converted by** `tools/stuntrally-car/convert.py`, which reads Ogre-Next v2 meshes. Each `.rcar`
  holds the body, interior, glass and wheel meshes, textures shrunk to 512 px, and wheel positions
  from the car's `.car` physics file.

Rebuild (needs `pip install pillow` and a checkout of stuntrally3):
    python3 tools/stuntrally-car/convert.py path/to/stuntrally3/data maps/cars LK4 XZ OT

## snowmountain.wad: Snow Mountain, a frozen tunnel

- **Source:** the driveline (`quads.xml`) of **Snow Mountain** from SuperTuxKart's track assets.
  Track by Canis Lupus, converted to SuperTuxKart by sj04736, released under **GPL and CC BY-SA**
  (full notice in `licenses/supertuxkart-snowmountain.txt`).
- **Only the road's shape is used.** The driveline lists the drivable surface as a chain of
  four-cornered patches with heights; no SuperTuxKart textures, models, sounds or music are included.
- **Licence of this file:** GPL-3.0-or-later and CC BY-SA, as a derivative of the track.

What changes from SuperTuxKart:
- The first 106 of the track's 168 patches are used, stopping before the road crosses over itself
  (Doom can't stack one road above another).
- Each patch becomes a sector at that height, squashed so no step is higher than 16 units.
- The road is enclosed as a low frozen tunnel, walled off at the start, with an exit switch at the end.
- Freedoom monsters (93, tougher further in), weapons, ammo, health and Doom 1 gore line the route.

Rebuild (needs `pip install numpy shapely` and SuperTuxKart's `tracks/snowmountain/quads.xml`):
    python3 tools/stk2doom/stk2doom.py path/to/snowmountain/quads.xml snow E1M1
    zdbsp -o maps/snowmountain.wad snow.raw.wad

## abuse.wad: Abuse (1995), level 1

- **Source:** **Abuse** by Crack dot Com, whose data was **released into the public domain**
  (see `licenses/abuse.txt`). Used: the layout of `data/levels/level01.spe` and the wall tiles from
  `data/art/fore/*.spe`. Abuse's music and sounds are not included.
- **Converted by** `tools/abuse2doom/abuse2doom.py`. Abuse is a side-scroller, so its level is read
  as a floor plan: tiles with a collision outline are solid, the rest is open floor, giving the
  level's real corridors and rooms. Every wall wears the actual Abuse tile behind it (21 different
  tiles, converted to the Doom palette and stored in the map with its own PNAMES/TEXTURE2), so it
  needs the built-in Freedoom.
- 73 Freedoom monsters face back along the route so they spot you coming; weapons, ammo, health,
  Doom 1 gore and lamps fill the tunnels, and the exit switch sits at the far end.

Rebuild (needs the Abuse data folder, freedoom1.wad and zdbsp):
    python3 tools/abuse2doom/abuse2doom.py path/to/abuse/data path/to/abuse/data/levels/level01.spe abuse E1M1 game/freedoom1.wad
    zdbsp -o maps/abuse.wad abuse.raw.wad
    python3 tools/abuse2doom/abuse2doom.py --addtex maps/abuse.wad abuse.tex
