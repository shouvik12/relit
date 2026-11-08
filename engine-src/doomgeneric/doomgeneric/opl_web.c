//
// Browser OPL driver: runs the DOSBox OPL emulator on demand. The page asks
// for audio a little ahead of time with dgx_music_render() and plays it with
// Web Audio. Based on Chocolate Doom's opl_sdl.c, without threads.
// Copyright(C) 2005-2014 Simon Howard; web port changes GPL-2.0-or-later.
//

#include <stdlib.h>
#include <string.h>

#include "opl.h"
#include "opl_internal.h"
#include "opl_queue.h"
#include "dbopl.h"

#define EXPORT(name) __attribute__((export_name(name)))

typedef struct
{
    unsigned int rate;
    unsigned int enabled;
    unsigned int value;
    uint64_t expire_time;
} opl_timer_t;

static opl_callback_queue_t *callback_queue;
static uint64_t current_time, pause_offset;
static int paused, opl3mode, register_num, mixing_freq = 44100, running;
static Chip chip;
static int32_t mix_buffer[4096 * 2];
static float out_buffer[4096 * 2];
static opl_timer_t timer1 = { 12500, 0, 0, 0 };
static opl_timer_t timer2 = { 3125, 0, 0, 0 };

static void AdvanceTime(unsigned int nsamples)
{
    opl_callback_t callback;
    void *data;
    uint64_t us = ((uint64_t) nsamples * OPL_SECOND) / mixing_freq;

    current_time += us;
    if (paused) pause_offset += us;

    while (!OPL_Queue_IsEmpty(callback_queue)
        && current_time >= OPL_Queue_Peek(callback_queue) + pause_offset)
    {
        if (!OPL_Queue_Pop(callback_queue, &callback, &data)) break;
        callback(data);
    }
}

static void Generate(float *out, unsigned int n, float gain)
{
    unsigned int i;
    if (opl3mode)
    {
        Chip__GenerateBlock3(&chip, n, mix_buffer);
        for (i = 0; i < n * 2; ++i) out[i] = mix_buffer[i] * gain;
    }
    else
    {
        Chip__GenerateBlock2(&chip, n, mix_buffer);
        for (i = 0; i < n; ++i) out[i * 2] = out[i * 2 + 1] = mix_buffer[i] * gain;
    }
}

// Renders `frames` stereo frames (max 4096) into a float buffer for the page.
EXPORT("dgx_music_render") float *dgx_music_render(int frames)
{
    unsigned int filled = 0;
    if (frames > 4096) frames = 4096;
    if (!running) { memset(out_buffer, 0, frames * 2 * sizeof(float)); return out_buffer; }
    while (filled < (unsigned int) frames)
    {
        uint64_t n;
        if (paused || OPL_Queue_IsEmpty(callback_queue))
        {
            n = frames - filled;
        }
        else
        {
            uint64_t next = OPL_Queue_Peek(callback_queue) + pause_offset;
            n = next > current_time ? (next - current_time) * mixing_freq : 0;
            n = (n + OPL_SECOND - 1) / OPL_SECOND;
            if (n > frames - filled) n = frames - filled;
        }
        if (n) Generate(out_buffer + filled * 2, (unsigned int) n, 1.0f / 32768.0f);
        filled += (unsigned int) n;
        AdvanceTime((unsigned int) n);
    }
    return out_buffer;
}

EXPORT("dgx_music_rate") int dgx_music_rate(void) { return mixing_freq; }

void OPL_Web_Delay(uint64_t us)
{
    // advance the emulated clock without producing audio
    unsigned int n = (unsigned int) ((us * mixing_freq + OPL_SECOND - 1) / OPL_SECOND);
    while (n > 0)
    {
        unsigned int k = n > 4096 ? 4096 : n;
        Generate(out_buffer, k, 0);
        AdvanceTime(k);
        n -= k;
    }
}

static int Web_Init(unsigned int port_base)
{
    (void) port_base;
    callback_queue = OPL_Queue_Create();
    current_time = pause_offset = 0;
    paused = 0;
    DBOPL_InitTables();
    Chip__Chip(&chip);
    Chip__Setup(&chip, mixing_freq);
    opl3mode = 0;
    running = 1;
    return 1;
}

static void Web_Shutdown(void)
{
    if (running) { OPL_Queue_Destroy(callback_queue); running = 0; }
}

static unsigned int Web_PortRead(opl_port_t port)
{
    unsigned int result = 0;
    if (port == OPL_REGISTER_PORT_OPL3) return 0xff;
    if (timer1.enabled && current_time > timer1.expire_time) result |= 0xc0;
    if (timer2.enabled && current_time > timer2.expire_time) result |= 0xa0;
    return result;
}

static void TimerEnd(opl_timer_t *t)
{
    if (t->enabled)
        t->expire_time = current_time + ((uint64_t) (0x100 - t->value) * OPL_SECOND) / t->rate;
}

static void WriteRegister(unsigned int reg, unsigned int value)
{
    switch (reg)
    {
        case OPL_REG_TIMER1: timer1.value = value; TimerEnd(&timer1); break;
        case OPL_REG_TIMER2: timer2.value = value; TimerEnd(&timer2); break;
        case OPL_REG_TIMER_CTRL:
            if (value & 0x80) { timer1.enabled = 0; timer2.enabled = 0; }
            else
            {
                if ((value & 0x40) == 0) { timer1.enabled = (value & 0x01) != 0; TimerEnd(&timer1); }
                if ((value & 0x20) == 0) { timer2.enabled = (value & 0x02) != 0; TimerEnd(&timer2); }
            }
            break;
        case OPL_REG_NEW:
            opl3mode = value & 0x01;
            // fall through
        default:
            Chip__WriteReg(&chip, reg, value);
            break;
    }
}

static void Web_PortWrite(opl_port_t port, unsigned int value)
{
    if (port == OPL_REGISTER_PORT) register_num = value;
    else if (port == OPL_REGISTER_PORT_OPL3) register_num = value | 0x100;
    else if (port == OPL_DATA_PORT) WriteRegister(register_num, value);
}

static void Web_SetCallback(uint64_t us, opl_callback_t cb, void *data)
{
    OPL_Queue_Push(callback_queue, cb, data, current_time - pause_offset + us);
}
static void Web_ClearCallbacks(void) { OPL_Queue_Clear(callback_queue); }
static void Web_Lock(void) {}
static void Web_Unlock(void) {}
static void Web_SetPaused(int p) { paused = p; }
static void Web_AdjustCallbacks(float factor) { OPL_Queue_AdjustCallbacks(callback_queue, current_time, factor); }

opl_driver_t opl_web_driver =
{
    "Web",
    Web_Init, Web_Shutdown, Web_PortRead, Web_PortWrite,
    Web_SetCallback, Web_ClearCallbacks, Web_Lock, Web_Unlock,
    Web_SetPaused, Web_AdjustCallbacks,
};
