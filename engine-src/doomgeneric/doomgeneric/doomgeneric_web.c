/*
 * Browser backend for doomgeneric, plus a state bridge for an external
 * renderer. GPL-2.0-or-later, same as the Doom source it links with.
 *
 * The page calls dgx_start() once and dgx_frame() every animation frame.
 * The original software renderer keeps drawing into DG_ScreenBuffer (used
 * for menus, intermission and "1993 mode"). The dgx_* exports copy the
 * live game state out so a separate renderer can draw the level itself.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "doomgeneric.h"
#include "doomkeys.h"
#include "doomstat.h"
#include "d_event.h"
#include "d_player.h"
#include "info.h"
#include "i_timer.h"
#include "p_local.h"
#include "r_state.h"
#include "w_wad.h"

#define EXPORT(name) __attribute__((export_name(name)))

/* ---- platform hooks ---------------------------------------------------- */

#define KEYQ 128
static int keyq[KEYQ][2];
static int keyhead, keytail;

void DG_Init(void) {}
void DG_DrawFrame(void) {}
void DG_SleepMs(uint32_t ms) { (void)ms; }
void DG_SetWindowTitle(const char *title) { (void)title; }

uint32_t DG_GetTicksMs(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000u + ts.tv_nsec / 1000000u);
}

int DG_GetKey(int *pressed, unsigned char *key)
{
    if (keyhead == keytail) return 0;
    *pressed = keyq[keytail][0];
    *key = (unsigned char)keyq[keytail][1];
    keytail = (keytail + 1) % KEYQ;
    return 1;
}

EXPORT("dgx_key") void dgx_key(int down, int key)
{
    int next = (keyhead + 1) % KEYQ;
    if (next == keytail) return;
    keyq[keyhead][0] = down;
    keyq[keyhead][1] = key;
    keyhead = next;
}

/* Mouse: horizontal motion turns, buttons map to fire/strafe/forward. */
EXPORT("dgx_mouse") void dgx_mouse(int dx, int buttons)
{
    event_t ev;
    ev.type = ev_mouse;
    ev.data1 = buttons;
    ev.data2 = dx;
    ev.data3 = 0;
    ev.data4 = 0;
    D_PostEvent(&ev);
}

/* Start the engine. The page puts the player's game file in the virtual
   file system as doom.wad (Doom 1 style, ExMy maps) or doom2.wad (Doom II
   style, MAPxx), which is also how the engine tells the two apart.
   With map > 0 it skips the title and starts that map at skill 1-5. */
EXPORT("dgx_start") void dgx_start(int episode, int map, int skill, int commercial)
{
    static char e[4], m[4], k[4];
    static char *argv[12];
    int argc = 0;
    argv[argc++] = "doom";
    argv[argc++] = "-iwad";
    argv[argc++] = commercial ? "doom2.wad" : "doom.wad";
    if (map > 0) {
        k[0] = '0' + skill;
        argv[argc++] = "-warp";
        if (commercial) {
            snprintf(m, sizeof m, "%d", map);
            argv[argc++] = m;
        } else {
            e[0] = '0' + episode; m[0] = '0' + map;
            argv[argc++] = e; argv[argc++] = m;
        }
        argv[argc++] = "-skill"; argv[argc++] = k;
    }
    argv[argc] = 0;
    doomgeneric_Create(argc, argv);
}

/* Only enter the engine when a new 35 Hz tic is due, so it never waits. */
static int last_time = -1;
EXPORT("dgx_frame") int dgx_frame(void)
{
    int now = I_GetTime();
    if (now == last_time) return 0;
    last_time = now;
    doomgeneric_Tick();
    return 1;
}

EXPORT("dgx_screen") uint32_t *dgx_screen(void) { return DG_ScreenBuffer; }
/* ---- state bridge ------------------------------------------------------ */

#define FX(v) ((float)(v) / 65536.0f)
#define ANG(v) ((float)((double)(uint32_t)(v) * (6.283185307179586 / 4294967296.0)))

/* [0] flags: bit0 in level, bit1 menu, bit2 automap, bit3 paused
   [1] episode [2] map [3] gametic [4] leveltime */
static int32_t info[8];
EXPORT("dgx_info") int32_t *dgx_info(void)
{
    info[0] = (gamestate == GS_LEVEL) | (menuactive << 1) | (automapactive << 2) | (paused << 3);
    info[1] = gameepisode;
    info[2] = gamemap;
    info[3] = gametic;
    info[4] = leveltime;
    info[5] = numsectors;
    info[6] = numsides;
    return info;
}

/* Player and HUD, as floats. */
static float pl[48];
EXPORT("dgx_player") float *dgx_player(void)
{
    player_t *p = &players[consoleplayer];
    memset(pl, 0, sizeof pl);
    if (!p->mo) return pl;
    pl[0] = FX(p->mo->x);
    pl[1] = FX(p->mo->y);
    pl[2] = FX(p->mo->z);
    pl[3] = FX(p->viewz);
    pl[4] = ANG(p->mo->angle);
    pl[5] = p->extralight;
    pl[6] = p->health;
    pl[7] = p->armorpoints;
    pl[8] = p->readyweapon;
    {
        ammotype_t a = weaponinfo[p->readyweapon].ammo;
        pl[9] = (a == am_noammo) ? -1 : p->ammo[a];
    }
    pl[10] = p->damagecount;
    pl[11] = p->bonuscount;
    pl[12] = p->fixedcolormap;
    for (int i = 0; i < 2; i++) {
        pspdef_t *ps = &p->psprites[i];
        if (ps->state) {
            pl[13 + i * 4] = ps->state->sprite;
            pl[14 + i * 4] = ps->state->frame;
            pl[15 + i * 4] = FX(ps->sx);
            pl[16 + i * 4] = FX(ps->sy);
        } else {
            pl[13 + i * 4] = -1;
        }
    }
    pl[21] = p->powers[pw_invulnerability];
    pl[22] = p->powers[pw_infrared];
    pl[23] = p->powers[pw_ironfeet];
    pl[24] = p->cards[it_bluecard] | p->cards[it_blueskull];
    pl[25] = p->cards[it_yellowcard] | p->cards[it_yellowskull];
    pl[26] = p->cards[it_redcard] | p->cards[it_redskull];
    pl[27] = p->killcount;  pl[28] = totalkills;
    pl[29] = p->itemcount;  pl[30] = totalitems;
    pl[31] = p->secretcount; pl[32] = totalsecret;
    pl[33] = p->playerstate;
    for (int i = 0; i < NUMAMMO; i++) { pl[34 + i] = p->ammo[i]; pl[38 + i] = p->maxammo[i]; }
    pl[42] = p->powers[pw_invisibility];
    pl[43] = p->powers[pw_strength];
    pl[44] = (p->mo->subsector) ? (float)(p->mo->subsector->sector - sectors) : 0;
    return pl;
}

/* Sectors: floor, ceiling, light, floor flat, ceiling flat (translated). */
static float *secbuf;
static int seccap;
EXPORT("dgx_sectors") float *dgx_sectors(void)
{
    if (numsectors * 5 > seccap) {
        seccap = numsectors * 5;
        secbuf = realloc(secbuf, seccap * sizeof(float));
    }
    for (int i = 0; i < numsectors; i++) {
        sector_t *s = &sectors[i];
        float *o = secbuf + i * 5;
        o[0] = FX(s->floorheight);
        o[1] = FX(s->ceilingheight);
        o[2] = s->lightlevel;
        o[3] = flattranslation[s->floorpic];
        o[4] = flattranslation[s->ceilingpic];
    }
    return secbuf;
}

/* Sides: top, bottom, mid texture (translated, 0 = none) and x offset. */
static int32_t *sidebuf;
static int sidecap;
EXPORT("dgx_sides") int32_t *dgx_sides(void)
{
    if (numsides * 4 > sidecap) {
        sidecap = numsides * 4;
        sidebuf = realloc(sidebuf, sidecap * sizeof(int32_t));
    }
    for (int i = 0; i < numsides; i++) {
        side_t *s = &sides[i];
        int32_t *o = sidebuf + i * 4;
        o[0] = s->toptexture ? texturetranslation[s->toptexture] : 0;
        o[1] = s->bottomtexture ? texturetranslation[s->bottomtexture] : 0;
        o[2] = s->midtexture ? texturetranslation[s->midtexture] : 0;
        o[3] = s->textureoffset >> FRACBITS;
    }
    return sidebuf;
}

/* Things as raw int32: id, x, y, z (16.16), angle (BAM), sprite, frame,
   flags, radius, height (16.16), type, sector index.
   Returns the count via dgx_thing_count(). The player's own body is skipped. */
#define THING_FIELDS 12
#define MAX_THINGS 4096
static int32_t things[MAX_THINGS * THING_FIELDS];
static int thingcount;
EXPORT("dgx_things") int32_t *dgx_things(void)
{
    thinker_t *th;
    mobj_t *self = players[consoleplayer].mo;
    thingcount = 0;
    if (gamestate != GS_LEVEL) return things;
    for (th = thinkercap.next; th != &thinkercap && thingcount < MAX_THINGS; th = th->next) {
        if (th->function.acp1 != (actionf_p1)P_MobjThinker) continue;
        mobj_t *m = (mobj_t *)th;
        if (m == self || (m->flags & MF_NOSECTOR)) continue;
        int32_t *o = things + thingcount * THING_FIELDS;
        o[0] = (int32_t)(uintptr_t)m;
        o[1] = m->x;
        o[2] = m->y;
        o[3] = m->z;
        o[4] = (int32_t)m->angle;
        o[5] = m->sprite;
        o[6] = m->frame;
        o[7] = m->flags;
        o[8] = m->radius;
        o[9] = m->height;
        o[10] = m->type;
        o[11] = m->subsector ? (int32_t)(m->subsector->sector - sectors) : 0;
        thingcount++;
    }
    return things;
}
EXPORT("dgx_thing_count") int dgx_thing_count(void) { return thingcount; }

/* Sprite names (SPR_*) so the page can find the sprite lumps. */
EXPORT("dgx_sprname") const char *dgx_sprname(int i)
{
    return (i >= 0 && i < NUMSPRITES) ? sprnames[i] : "";
}
EXPORT("dgx_numsprites") int dgx_numsprites(void) { return NUMSPRITES; }

/* WASI has no shell; Doom only calls this for an optional error dialog. */
int system(const char *cmd) { (void)cmd; return -1; }

/* Pickup and status messages, counted so the page can spot new ones. */
static char lastmsg[128];
static int msgcount;
void dgx_note_message(const char *msg)
{
    strncpy(lastmsg, msg ? msg : "", sizeof lastmsg - 1);
    msgcount++;
}
EXPORT("dgx_message") const char *dgx_message(void) { return lastmsg; }
EXPORT("dgx_message_count") int dgx_message_count(void) { return msgcount; }
