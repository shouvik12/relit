# Relit

**The 1993 Doom engine, relit for today.**

The original Doom engine running in your browser, drawn by a brand-new 3D renderer. Nothing to install.

id Software's 1993 engine still runs the game, unchanged: the physics, monsters, weapons and doors all behave exactly like the original. Every frame, a new renderer written in Three.js reads the live game state and draws the world in real 3D:

- **Free mouselook:** look up and down, not just left and right
- **A flashlight with real shadows:** walls, pillars and monsters all block it (press `L`)
- **Moving lights:** fireballs, rockets, plasma, bullet impacts, torches and lamps light up the walls around them, and the muzzle flash lights the room
- **Ambient occlusion:** soft contact shadows in corners and where walls meet floors
- **AI-remastered art:** every wall, floor, sky, monster and weapon is redrawn at 4x resolution by an AI upscaling model (Real-ESRGAN) running on the player's own GPU. The game starts at once and the art sharpens as it finishes; results are kept in the browser, so later launches are remastered from the first frame
- **Surface detail:** every texture gets a normal map, so lights catch its bumps
- **Atmosphere:** sparks and smoke where bullets hit, fiery bursts and screen shake from explosions, embers rising from torches, dust drifting in the flashlight beam, bloom, fog and glowing liquids
- **Weapon feel:** recoil kick, sway as you turn, and a muzzle glow on every shot
- **3D sound:** every sound effect is placed around you with HRTF spatial audio, through a reverb that follows the size of the room, plus the music played on an emulated AdLib (OPL) chip, like the original
- **Smooth motion:** the engine runs at its original 35 tics per second, and the renderer interpolates between tics for 60 fps and above
- **The 1993 view, one key away:** press `V` to flip to the original software renderer, live

Players bring the game data: [Freedoom](https://freedoom.github.io/), a free replacement for Doom's levels, monsters and art, or their own copy of Doom or Doom II.

## Play it

Relit is one file, `relit.html`. There's no website and no server: it runs entirely in the player's own browser.

1. Double-click `relit.html` to open it in Chrome, Edge, Firefox or Safari.
2. Click **Choose your game file** and pick a Doom-format `.wad` file:
   - **Freedoom** (free): download it from [freedoom.github.io](https://freedoom.github.io/download.html), unzip it, and pick `freedoom1.wad` or `freedoom2.wad`.
   - **Your own Doom**: `doom.wad`, `doom1.wad` or `doom2.wad` from a copy you own.
3. Play. The browser remembers the file, so next time it's one click. **Forget this file** clears it.

The game file is read inside the browser and never uploaded anywhere. Relit ships no game data at all.

## Share it

Send `relit.html` together with `relit-source.zip`. Relit is GPL software, so the source has to travel with the game. Anywhere that can hold two files works: itch.io (it hosts HTML games), Google Drive, Discord, or a download link in an X post.

## Build it from source

```bash
npm install
npm run libs     # bundles Three.js, earcut and the WASI shim into vendor/libs.js
npm run engine   # optional: rebuilds doom.wasm (needs: pip install ziglang)
npm run build    # writes dist/relit.html
```

For development, serve the folder and open `index.html` (`python3 -m http.server 8000`).

## Controls

| Action | Keyboard and mouse | Phone |
|---|---|---|
| Move | `W` `A` `S` `D` or arrows | Drag on the left half |
| Look | Mouse (click the game first) | Drag on the right half |
| Fire | Left click or `Ctrl` | Fire button |
| Open doors, switches | `Space`, `E` or right click | Use button |
| Weapons | `1` to `7` or the mouse wheel | Weapon button |
| Flashlight | `L` | Flashlight button |
| 1993 view | `V` | 1993 view button |
| Menu | `Esc` | Menu button |

## How it works

| Path | What it is | Licence |
|---|---|---|
| `index.html`, `main.js` | Start screen, game file picker, HUD and controls | GPL-2.0 |
| `remaster.js` | The new renderer: WAD reader, level geometry, lighting, sprites, weapon, input | GPL-2.0 |
| `ai.js`, `relit-ai.bin` | AI upscaler as WebGL2 shaders, with its job queue and browser cache; the realesr-general-x4v3 model weights | GPL-2.0 (code), BSD-3-Clause (weights) |
| `audio.js` | Web Audio: 3D sound effects, room reverb, streamed music | GPL-2.0 |
| `effects.js` | Particles: sparks, smoke, bursts, embers, dust | GPL-2.0 |
| `tools/ai/` | Python scripts that load the model and pack its weights for the browser | GPL-2.0 |
| `doom.wasm` | The Doom engine compiled to WebAssembly | GPL-2.0 |
| `engine-src/` | Full engine source and `build.sh` | GPL-2.0 |
| `tools/make-single.mjs` | Packs everything into `dist/relit.html` | GPL-2.0 |
| `vendor/libs.js` | Three.js 0.170, earcut 3.0.1, browser_wasi_shim 0.4.2, bundled | MIT, ISC, MIT or Apache-2.0 |

The engine is [doomgeneric](https://github.com/ozkl/doomgeneric) (commit `e257a09`), a portable version of Chocolate Doom, which is built on the source id Software released under the GPL. Changes from upstream:

- `doomgeneric_web.c` (new): the browser backend, plus a state bridge that hands the page the player, every thing, every sector's heights, lights and flats, and every wall's textures
- `g_game.c`: mouse turning adds up between tics instead of overwriting
- `d_main.c`: the screen-melt transition is turned off, since it blocks the browser
- `hu_stuff.c`: pickup messages are copied out for the HUD
- `sound_web.c` (new) and `s_sound.c`: sound effects go to the page along with where each one comes from
- `opl_web.c` (new): runs the OPL music emulator on demand for the page
- `opl.c`, `dbopl.c`, `opl_queue.c`, `i_oplmusic.c`, `midifile.c` (with small changes) and their headers: the OPL music player from [Chocolate Doom](https://github.com/chocolate-doom/chocolate-doom) 2.2.1, with the DOSBox OPL emulator, all GPL
- `i_sound.c`, `i_sound.h`: sound built in without SDL, and the music module started properly

To rebuild the engine: `pip install ziglang`, then run `engine-src/build.sh`.

The renderer reads the map straight from the WAD file. It traces each sector's outline and triangulates it into floors and ceilings, and builds walls with Doom's texture alignment rules. Then it updates everything live from the engine: doors, lifts, switches, flickering lights, monsters and projectiles.

### The AI remaster

The model is [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)'s compact `realesr-general-x4v3` by Xintao Wang and others (BSD-3-Clause, see `licenses/`). `ai.js` runs it with plain WebGL2: each of its 34 layers is one shader pass, with the 64 channels packed as 16 RGBA tiles. The work is spread across frames and read back asynchronously, so the game keeps running while the art upgrades. The output matches a reference PyTorch-free NumPy run of the model to within 1/255. Add `?noai` to the page's address to switch it off.

To repack the weights: download `realesr-general-x4v3.pth` from the Real-ESRGAN releases and run `python3 tools/ai/export.py realesr-general-x4v3.pth relit-ai.bin`.

## Known gaps

- Monsters and items are still flat sprites, now AI-sharpened, lit and shadowed in 3D
- No screen-space reflections yet
- Looking up and down is visual. Shots aim the way Doom always has, with automatic vertical aim
- Phones skip the flashlight shadows and ambient occlusion to keep the frame rate up
- Menus, the automap and the end-of-level screen use the original 1993 renderer

## Not affiliated

This project is not affiliated with or endorsed by id Software, Bethesda or ZeniMax. Doom is a trademark of its owners and is named only to describe the engine. No game data is included; players load their own.
