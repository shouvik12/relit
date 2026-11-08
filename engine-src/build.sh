#!/bin/sh
# Builds the Doom engine for the browser with Zig (pip install ziglang).
set -e
cd "$(dirname "$0")/doomgeneric/doomgeneric"
python3 -m ziglang cc -target wasm32-wasi -mexec-model=reactor -O2 -std=gnu99 -w \
  -D_WASI_EMULATED_SIGNAL -D_WASI_EMULATED_PROCESS_CLOCKS -D_WASI_EMULATED_MMAN -DFEATURE_SOUND -DDOOMGENERIC_RESX=640 -DDOOMGENERIC_RESY=400 \
  dummy.c am_map.c doomdef.c doomstat.c dstrings.c d_event.c d_items.c d_iwad.c d_loop.c d_main.c d_mode.c d_net.c f_finale.c f_wipe.c g_game.c hu_lib.c hu_stuff.c info.c i_cdmus.c i_endoom.c i_joystick.c i_scale.c i_sound.c i_system.c i_timer.c memio.c m_argv.c m_bbox.c m_cheat.c m_config.c m_controls.c m_fixed.c m_menu.c m_misc.c m_random.c p_ceilng.c p_doors.c p_enemy.c p_floor.c p_inter.c p_lights.c p_map.c p_maputl.c p_mobj.c p_plats.c p_pspr.c p_saveg.c p_setup.c p_sight.c p_spec.c p_switch.c p_telept.c p_tick.c p_user.c r_bsp.c r_data.c r_draw.c r_main.c r_plane.c r_segs.c r_sky.c r_things.c sha1.c sounds.c statdump.c st_lib.c st_stuff.c s_sound.c tables.c v_video.c wi_stuff.c w_checksum.c w_file.c w_main.c w_wad.c z_zone.c w_file_stdc.c i_input.c i_video.c doomgeneric.c  doomgeneric_web.c sound_web.c opl.c dbopl.c opl_queue.c opl_web.c midifile.c i_oplmusic.c mus2mid.c \
  -lwasi-emulated-signal -lwasi-emulated-process-clocks -lwasi-emulated-mman \
  -Wl,-z,stack-size=2097152 -Wl,--initial-memory=67108864 -Wl,--max-memory=268435456 -s \
  -o ../../../doom.wasm
