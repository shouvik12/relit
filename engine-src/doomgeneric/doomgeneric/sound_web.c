/*
 * Browser sound for doomgeneric: sound effects are handed to the page, which
 * plays them with Web Audio, positioned in 3D around the player. Music is
 * synthesised here with an OPL (AdLib) emulator and streamed to the page.
 * GPL-2.0-or-later, same as the Doom source it links with.
 */

#include <stdio.h>
#include <string.h>

#include "doomtype.h"
#include "i_sound.h"
#include "m_misc.h"
#include "w_wad.h"
#include "p_mobj.h"
#include "deh_str.h"

#define IMPORT(name) __attribute__((import_module("relit"), import_name(name)))

IMPORT("sfx_start") void js_sfx_start(int channel, int lump, int vol, int sep, int haspos, float x, float y, float z);
IMPORT("sfx_update") void js_sfx_update(int channel, int vol, int sep, int haspos, float x, float y, float z);
IMPORT("sfx_stop") void js_sfx_stop(int channel);
IMPORT("sfx_playing") int js_sfx_playing(int channel);

extern mobj_t *dgx_sound_origin;

int use_libsamplerate = 0;
float libsamplerate_scale = 0.65f;
void I_InitTimidityConfig(void) {}

static boolean use_prefix;

static boolean Web_Init(boolean use_sfx_prefix) { use_prefix = use_sfx_prefix; return true; }
static void Web_Shutdown(void) {}
static void Web_Update(void) {}
static void Web_Precache(sfxinfo_t *sounds, int n) { (void)sounds; (void)n; }

static int Web_GetSfxLumpNum(sfxinfo_t *sfx)
{
    char name[16];
    if (sfx->link) sfx = sfx->link;
    if (use_prefix) M_snprintf(name, sizeof name, "ds%s", DEH_String(sfx->name));
    else M_StringCopy(name, DEH_String(sfx->name), sizeof name);
    return W_CheckNumForName(name);
}

#define POS(m) (m) ? 1 : 0, (m) ? (float)(m)->x / 65536.0f : 0, (m) ? (float)(m)->y / 65536.0f : 0, \
               (m) ? (float)((m)->z + (m)->height / 2) / 65536.0f : 0

static int Web_StartSound(sfxinfo_t *sfx, int channel, int vol, int sep)
{
    mobj_t *o = dgx_sound_origin;
    if (sfx->lumpnum < 0) return -1;
    js_sfx_start(channel, sfx->lumpnum, vol, sep, POS(o));
    return channel;
}

static void Web_UpdateSoundParams(int channel, int vol, int sep)
{
    mobj_t *o = dgx_sound_origin;
    js_sfx_update(channel, vol, sep, POS(o));
}

static void Web_StopSound(int channel) { js_sfx_stop(channel); }
static boolean Web_SoundIsPlaying(int channel) { return js_sfx_playing(channel) != 0; }

static snddevice_t devices[] = { SNDDEVICE_SB, SNDDEVICE_PAS, SNDDEVICE_GUS, SNDDEVICE_WAVEBLASTER,
                                  SNDDEVICE_SOUNDCANVAS, SNDDEVICE_AWE32 };

sound_module_t DG_sound_module = {
    devices, sizeof devices / sizeof *devices,
    Web_Init, Web_Shutdown, Web_GetSfxLumpNum, Web_Update, Web_UpdateSoundParams,
    Web_StartSound, Web_StopSound, Web_SoundIsPlaying, Web_Precache,
};
