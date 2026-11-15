/*
 * Relit car mode: the player drives instead of walking, on one chosen map.
 * GPL-2.0-or-later, same as the Doom source it links with.
 *
 * The page turns it on with dgx_car() before the engine starts. Everything else in the
 * game is untouched: monsters, damage, pickups and the exit are plain Doom.
 */
#include <stdlib.h>

#include "doomdef.h"
#include "doomstat.h"
#include "d_event.h"
#include "info.h"
#include "m_fixed.h"
#include "p_local.h"
#include "r_state.h"
#include "tables.h"

#include "relit_car.h"

#define EXPORT(name) __attribute__((export_name(name)))
#define U(x) ((fixed_t)((x) * FRACUNIT))

extern boolean onground;                                   /* p_user.c */
void P_Thrust(player_t *player, angle_t angle, fixed_t move);

static int car_on, car_map;
int relit_car_dark;

#define TRAIL 16
static struct { fixed_t x, y; angle_t angle; } trail[TRAIL];
static int trail_n, trail_level = -1, reset_asked, rocket_tic, ram_tic;
static mobj_t *ram_thing;

int Car_Active(void)
{
    return car_on && gamemode == commercial && gamemap == car_map;
}

EXPORT("dgx_car") void dgx_car(int on, int map)
{
    car_on = on; car_map = map;
    /* a car is wider than a marine */
    mobjinfo[MT_PLAYER].radius = (on ? 24 : 16) * FRACUNIT;
}
EXPORT("dgx_car_lights") void dgx_car_lights(int on) { relit_car_dark = !on; }
EXPORT("dgx_car_reset") void dgx_car_reset(void) { reset_asked = 1; }

static fixed_t forward_speed(mobj_t *mo)
{
    unsigned an = mo->angle >> ANGLETOFINESHIFT;
    return FixedMul(mo->momx, finecosine[an]) + FixedMul(mo->momy, finesine[an]);
}

void Car_Move(player_t *player)
{
    ticcmd_t *cmd = &player->cmd;
    mobj_t *mo = player->mo;
    fixed_t vf = forward_speed(mo), av = abs(vf), f;
    int turn = cmd->angleturn;

    /* steering: the car turns on the spot when stopped, like Doom's player,
       is sharpest at walking pace and gets heavier with speed */
    if (turn > 640) turn = 640;
    if (turn < -640) turn = -640;
    f = av >= U(5) ? FRACUNIT : U(0.7) + FixedMul(av / 5, U(0.3));
    f = FixedDiv(f, FRACUNIT + av / 22);
    if (vf < 0) turn = -turn;
    mo->angle += (angle_t)((int)(((long long)turn * f) >> FRACBITS) << 16);

    onground = (mo->z <= mo->floorz);
    if (!onground) return;
    if (cmd->forwardmove > 0)
        P_Thrust(player, mo->angle, U(0.46));
    else if (cmd->forwardmove < 0) {
        if (vf > U(1)) P_Thrust(player, mo->angle, -U(1.1));      /* brakes */
        else if (vf > -U(9)) P_Thrust(player, mo->angle, -U(0.3)); /* reverse */
    }
}

void Car_Friction(mobj_t *mo)
{
    unsigned an = mo->angle >> ANGLETOFINESHIFT;
    fixed_t c = finecosine[an], s = finesine[an];
    fixed_t vf = FixedMul(mo->momx, c) + FixedMul(mo->momy, s);
    fixed_t vl = FixedMul(mo->momx, s) - FixedMul(mo->momy, c);
    int offroad = mo->subsector->sector->tag == CAR_OFFROAD_TAG;
    int gas = mo->player->cmd.forwardmove != 0;

    vl = FixedMul(vl, offroad ? U(0.90) : U(0.80));          /* tyres resist sliding sideways */
    vf = FixedMul(vf, offroad ? U(0.962) : U(0.984));        /* drag sets the top speed */
    if (!gas) vf = FixedMul(vf, U(0.99));
    if (!gas && abs(vf) < U(0.2) && abs(vl) < U(0.2)) vf = vl = 0;
    mo->momx = FixedMul(vf, c) + FixedMul(vl, s);
    mo->momy = FixedMul(vf, s) - FixedMul(vl, c);
}

void Car_Think(player_t *player)
{
    mobj_t *mo = player->mo;
    int i;

    if (trail_level != gamemap * 1000 + gameepisode || leveltime < 2) {
        trail_level = gamemap * 1000 + gameepisode; trail_n = 0; rocket_tic = 0;
    }
    /* loadout: roof gun and rocket pods, once per life */
    if (!player->backpack) {
        player->backpack = true;
        for (i = 0; i < NUMAMMO; i++) player->maxammo[i] *= 2;
        player->weaponowned[wp_chaingun] = player->weaponowned[wp_missile] = true;
        player->ammo[am_clip] = 300; player->ammo[am_misl] = 20;
        player->armorpoints = 100; player->armortype = 1;
        player->pendingweapon = wp_chaingun;
    }
    if (player->readyweapon != wp_chaingun && player->pendingweapon == wp_nochange && player->ammo[am_clip] > 0)
        player->pendingweapon = wp_chaingun;

    /* rocket pods on the second trigger, whatever the roof gun is doing */
    if ((player->cmd.buttons & BT_USE) && leveltime >= rocket_tic && player->ammo[am_misl] > 0) {
        player->ammo[am_misl]--;
        P_SpawnPlayerMissile(mo, MT_ROCKET);
        P_NoiseAlert(mo, mo);
        rocket_tic = leveltime + 16;
    }

    /* remember where the road was, so a stranded car can be put back */
    if (!(leveltime % 18) && mo->z <= mo->floorz && mo->subsector->sector->tag != CAR_OFFROAD_TAG
        && mo->subsector->sector->special == 0) {
        if (trail_n == TRAIL) { for (i = 1; i < TRAIL; i++) trail[i - 1] = trail[i]; trail_n--; }
        trail[trail_n].x = mo->x; trail[trail_n].y = mo->y; trail[trail_n].angle = mo->angle; trail_n++;
    }
    if (reset_asked) {
        reset_asked = 0;
        if (trail_n > 0) {
            i = trail_n > 3 ? trail_n - 3 : 0;
            if (P_TeleportMove(mo, trail[i].x, trail[i].y)) {
                mo->z = mo->floorz; mo->angle = trail[i].angle;
                mo->momx = mo->momy = mo->momz = 0;
                player->viewz = mo->z + player->viewheight;
                trail_n = i + 1;
            }
        }
    }
}

void Car_Ram(mobj_t *car, mobj_t *thing)
{
    fixed_t speed = P_AproxDistance(car->momx, car->momy);
    if (speed < U(9) || thing->health <= 0) return;
    if (ram_tic == gametic && ram_thing == thing) return;   /* once per tic per victim */
    ram_tic = gametic; ram_thing = thing;
    P_DamageMobj(thing, car, car, (speed >> FRACBITS) * 6);
    P_DamageMobj(car, NULL, NULL, thing->health > 0 ? 6 : 2);
    car->momx = FixedMul(car->momx, U(0.8)); car->momy = FixedMul(car->momy, U(0.8));
}
