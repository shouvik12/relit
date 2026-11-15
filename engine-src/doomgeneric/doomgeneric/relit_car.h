/*
 * Relit car mode: the player drives instead of walking, on one chosen map.
 * GPL-2.0-or-later, same as the Doom source it links with.
 */
#ifndef RELIT_CAR_H
#define RELIT_CAR_H

#include "d_player.h"
#include "p_mobj.h"

#define CAR_OFFROAD_TAG 900   /* sectors with this tag are off the road: less grip, more drag */

extern int relit_car_dark;    /* headlights off: sleeping monsters only notice the car up close */

int  Car_Active(void);
void Car_Move(player_t *player);            /* steering and throttle, instead of walking */
void Car_Friction(mobj_t *mo);              /* grip and drag, instead of Doom's friction */
void Car_Think(player_t *player);           /* loadout, rocket pods, road trail */
void Car_Ram(mobj_t *car, mobj_t *thing);   /* driving into something shootable */

#endif
