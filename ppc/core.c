/* A PowerPC user-mode core in C, matching ppc/cpu.py instruction for
 * instruction.
 *
 * The Python interpreter is the reference: it is the thing that was debugged
 * against VocalWriter's own output, and this file exists only to run the same
 * decisions faster. Every case below mirrors the corresponding branch there,
 * including the parts that look odd -- the single-precision rounding on opcode
 * 59, the integer left in the low word of an FPR by fctiwz, the saturation of
 * stfs to infinity -- because those were each arrived at by finding audio that
 * came out wrong without them.
 *
 * Control returns to Python for three things: reaching the return sentinel,
 * hitting an address Python has hooked (the allocator, libm, the file calls),
 * and anything unhandled. Hooks are rare, so the check is one compare against
 * the lowest hooked address before any lookup happens.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define PAGE_BITS 16
#define PAGE_SIZE (1 << PAGE_BITS)
#define PAGE_MASK (PAGE_SIZE - 1)
#define NPAGES    (1 << (32 - PAGE_BITS))
#define SENTINEL  0xDEAD0000u
#define MAXHOOKS  64

struct CPU {
    uint32_t r[32];
    double   f[32];
    uint8_t  cr[8][3];          /* lt, gt, eq */
    uint32_t lr, ctr, xer, pc;
    uint64_t steps;
    uint8_t *pages[NPAGES];
    uint32_t hooks[MAXHOOKS];
    int      nhooks;
    uint32_t hookmin;
    uint32_t overflows;         /* stfs saturations, as a health signal */
    int      err_op;            /* what was unhandled, for the message */
    int      err_xo;
};
typedef struct CPU CPU;

/* ---------------------------------------------------------------- memory */

static uint8_t *page_of(CPU *c, uint32_t a)
{
    uint32_t p = a >> PAGE_BITS;
    uint8_t *pg = c->pages[p];
    if (!pg) {
        pg = (uint8_t *)calloc(PAGE_SIZE, 1);
        c->pages[p] = pg;
    }
    return pg;
}

static inline uint8_t rd8(CPU *c, uint32_t a)
{
    return page_of(c, a)[a & PAGE_MASK];
}

static inline void wr8(CPU *c, uint32_t a, uint8_t v)
{
    page_of(c, a)[a & PAGE_MASK] = v;
}

static inline uint32_t rd16(CPU *c, uint32_t a)
{
    uint32_t o = a & PAGE_MASK;
    if (o <= PAGE_SIZE - 2) {
        uint8_t *p = page_of(c, a) + o;
        return ((uint32_t)p[0] << 8) | p[1];
    }
    return ((uint32_t)rd8(c, a) << 8) | rd8(c, a + 1);
}

static inline void wr16(CPU *c, uint32_t a, uint32_t v)
{
    uint32_t o = a & PAGE_MASK;
    if (o <= PAGE_SIZE - 2) {
        uint8_t *p = page_of(c, a) + o;
        p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v;
    } else {
        wr8(c, a, (uint8_t)(v >> 8)); wr8(c, a + 1, (uint8_t)v);
    }
}

static inline uint32_t rd32(CPU *c, uint32_t a)
{
    uint32_t o = a & PAGE_MASK;
    if (o <= PAGE_SIZE - 4) {
        uint8_t *p = page_of(c, a) + o;
        return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16)
             | ((uint32_t)p[2] << 8) | p[3];
    }
    return (rd16(c, a) << 16) | rd16(c, a + 2);
}

static inline void wr32(CPU *c, uint32_t a, uint32_t v)
{
    uint32_t o = a & PAGE_MASK;
    if (o <= PAGE_SIZE - 4) {
        uint8_t *p = page_of(c, a) + o;
        p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16);
        p[2] = (uint8_t)(v >> 8);  p[3] = (uint8_t)v;
    } else {
        wr16(c, a, v >> 16); wr16(c, a + 2, v & 0xFFFF);
    }
}

static inline uint64_t rd64(CPU *c, uint32_t a)
{
    return ((uint64_t)rd32(c, a) << 32) | rd32(c, a + 4);
}

static inline void wr64(CPU *c, uint32_t a, uint64_t v)
{
    wr32(c, a, (uint32_t)(v >> 32)); wr32(c, a + 4, (uint32_t)v);
}

static inline double rdf32(CPU *c, uint32_t a)
{
    union { uint32_t u; float g; } x; x.u = rd32(c, a); return (double)x.g;
}

static inline void wrf32(CPU *c, uint32_t a, double v)
{
    union { uint32_t u; float g; } x;
    /* A double too large for single precision becomes an infinity on PowerPC
     * rather than trapping, which is what the cast does here too. */
    if (v > 3.4028235677973366e38) { x.g = (float)INFINITY; c->overflows++; }
    else if (v < -3.4028235677973366e38) { x.g = -(float)INFINITY; c->overflows++; }
    else x.g = (float)v;
    wr32(c, a, x.u);
}

static inline double rdf64(CPU *c, uint32_t a)
{
    union { uint64_t u; double g; } x; x.u = rd64(c, a); return x.g;
}

static inline void wrf64(CPU *c, uint32_t a, double v)
{
    union { uint64_t u; double g; } x; x.g = v; wr64(c, a, x.u);
}

/* ------------------------------------------------------------- helpers */

static inline int32_t s32(uint32_t v) { return (int32_t)v; }
static inline int32_t s16v(uint32_t w) { return (int32_t)(int16_t)(w & 0xFFFF); }

static inline void setcr(CPU *c, int field, int64_t a, int64_t b)
{
    c->cr[field][0] = a < b; c->cr[field][1] = a > b; c->cr[field][2] = a == b;
}

static inline double single(double v)
{
    if (v > 3.4028235677973366e38) return (double)INFINITY;
    if (v < -3.4028235677973366e38) return -(double)INFINITY;
    return (double)(float)v;
}

/* fctiwz leaves the converted word in the low half of the register, and the
 * code stores it with stfd and reads that word back. Keeping it as the
 * register's bit pattern reproduces that exactly. */
static inline double int_pattern(int32_t iv)
{
    union { uint64_t u; double g; } x;
    x.u = (uint64_t)(uint32_t)iv;
    return x.g;
}

static inline int32_t to_int(double v, int trunc)
{
    if (isnan(v)) return INT32_MIN;
    if (v >= 2147483647.0) return INT32_MAX;
    if (v <= -2147483648.0) return INT32_MIN;
    return trunc ? (int32_t)v : (int32_t)llround(v);
}

static uint32_t maskbits(int mb, int me)
{
    uint32_t x, y;
    if (mb <= me) {
        x = 0xFFFFFFFFu >> mb;
        y = (31 - me) >= 32 ? 0 : (0xFFFFFFFFu << (31 - me));
        return x & y;
    }
    x = (me + 1) >= 32 ? 0 : (0xFFFFFFFFu >> (me + 1));
    y = (32 - mb) >= 32 ? 0 : (0xFFFFFFFFu << (32 - mb));
    return ~(x & y);
}

static int cond(CPU *c, int bo, int bi)
{
    int ctr_ok = 1;
    if (!(bo & 0x04)) {
        c->ctr = (c->ctr - 1) & 0xFFFFFFFFu;
        ctr_ok = (bo & 0x02) ? (c->ctr == 0) : (c->ctr != 0);
    }
    if (bo & 0x10) return ctr_ok;
    {
        int field = bi >> 2, bit = bi & 3;
        int val = (bit < 3) ? c->cr[field][bit] : 0;
        int want = (bo & 0x08) ? 1 : 0;
        return ctr_ok && (val == want);
    }
}

/* ---------------------------------------------------------------- public */

CPU *cpu_new(void)
{
    CPU *c = (CPU *)calloc(1, sizeof(CPU));
    c->hookmin = SENTINEL;
    return c;
}

void cpu_free(CPU *c)
{
    int i;
    if (!c) return;
    for (i = 0; i < NPAGES; i++) if (c->pages[i]) free(c->pages[i]);
    free(c);
}

void mem_write(CPU *c, uint32_t a, const char *buf, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++) wr8(c, a + i, (uint8_t)buf[i]);
}

void mem_read(CPU *c, uint32_t a, char *buf, uint32_t n)
{
    uint32_t i;
    for (i = 0; i < n; i++) buf[i] = (char)rd8(c, a + i);
}

uint32_t mem_r8(CPU *c, uint32_t a)  { return rd8(c, a); }
uint32_t mem_r16(CPU *c, uint32_t a) { return rd16(c, a); }
uint32_t mem_r32(CPU *c, uint32_t a) { return rd32(c, a); }
void mem_w8(CPU *c, uint32_t a, uint32_t v)  { wr8(c, a, (uint8_t)v); }
void mem_w16(CPU *c, uint32_t a, uint32_t v) { wr16(c, a, v); }
void mem_w32(CPU *c, uint32_t a, uint32_t v) { wr32(c, a, v); }
double mem_rf32(CPU *c, uint32_t a) { return rdf32(c, a); }
void mem_wf32(CPU *c, uint32_t a, double v) { wrf32(c, a, v); }
double mem_rf64(CPU *c, uint32_t a) { return rdf64(c, a); }
void mem_wf64(CPU *c, uint32_t a, double v) { wrf64(c, a, v); }

void cpu_set_hooks(CPU *c, const uint32_t *addrs, int n)
{
    int i;
    if (n > MAXHOOKS) n = MAXHOOKS;
    c->nhooks = n;
    c->hookmin = SENTINEL;
    for (i = 0; i < n; i++) {
        c->hooks[i] = addrs[i];
        if (addrs[i] < c->hookmin) c->hookmin = addrs[i];
    }
}

uint32_t cpu_get_reg(CPU *c, int i)            { return c->r[i]; }
void     cpu_set_reg(CPU *c, int i, uint32_t v){ c->r[i] = v; }
double   cpu_get_f(CPU *c, int i)              { return c->f[i]; }
void     cpu_set_f(CPU *c, int i, double v)    { c->f[i] = v; }
uint32_t cpu_get_pc(CPU *c)                    { return c->pc; }
void     cpu_set_pc(CPU *c, uint32_t v)        { c->pc = v; }
uint32_t cpu_get_lr(CPU *c)                    { return c->lr; }
void     cpu_set_lr(CPU *c, uint32_t v)        { c->lr = v; }
uint64_t cpu_get_steps(CPU *c)                 { return c->steps; }
uint32_t cpu_overflows(CPU *c)                 { return c->overflows; }
int      cpu_err_op(CPU *c)                    { return c->err_op; }
int      cpu_err_xo(CPU *c)                    { return c->err_xo; }

/* 0 = returned to the sentinel, 1 = hook, 2 = step limit, 3 = unhandled */
int cpu_run(CPU *c, uint64_t max_steps)
{
    uint64_t n = 0;
    uint32_t *r = c->r;
    double *f = c->f;

    for (;;) {
        uint32_t pc = c->pc, word, nxt;
        int op;

        if (pc == SENTINEL) return 0;
        if (pc >= c->hookmin) {
            int i;
            for (i = 0; i < c->nhooks; i++)
                if (c->hooks[i] == pc) return 1;
        }
        if (n++ > max_steps) return 2;
        c->steps++;

        word = rd32(c, pc);
        nxt = pc + 4;
        op = word >> 26;

        switch (op) {
        case 32: { /* lwz */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            r[d] = rd32(c, (a ? r[a] : 0) + s16v(word));
            break; }
        case 40: { /* lhz */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            r[d] = rd16(c, (a ? r[a] : 0) + s16v(word));
            break; }
        case 48: { /* lfs */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            f[d] = rdf32(c, (a ? r[a] : 0) + s16v(word));
            break; }
        case 14: { /* addi */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            r[d] = (a ? r[a] : 0) + s16v(word);
            break; }
        case 52: { /* stfs */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            wrf32(c, (a ? r[a] : 0) + s16v(word), f[s]);
            break; }
        case 44: { /* sth */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            wr16(c, (a ? r[a] : 0) + s16v(word), r[s] & 0xFFFF);
            break; }
        case 36: { /* stw */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            wr32(c, (a ? r[a] : 0) + s16v(word), r[s]);
            break; }
        case 15: { /* addis */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            r[d] = (a ? r[a] : 0) + (uint32_t)(s16v(word) << 16);
            break; }
        case 34: { /* lbz */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            r[d] = rd8(c, (a ? r[a] : 0) + s16v(word));
            break; }
        case 38: { /* stb */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            wr8(c, (a ? r[a] : 0) + s16v(word), r[s] & 0xFF);
            break; }
        case 42: { /* lha */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            r[d] = (uint32_t)(int32_t)(int16_t)rd16(
                c, (a ? r[a] : 0) + s16v(word));
            break; }
        case 50: { /* lfd */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            f[d] = rdf64(c, (a ? r[a] : 0) + s16v(word));
            break; }
        case 54: { /* stfd */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            wrf64(c, (a ? r[a] : 0) + s16v(word), f[s]);
            break; }
        case 33: { /* lwzu */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            uint32_t ea = r[a] + s16v(word);
            r[d] = rd32(c, ea); r[a] = ea;
            break; }
        case 37: { /* stwu */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            uint32_t ea = r[a] + s16v(word);
            wr32(c, ea, r[s]); r[a] = ea;
            break; }
        case 46: { /* lmw */
            int d = (word >> 21) & 31, a = (word >> 16) & 31, i;
            uint32_t ea = (a ? r[a] : 0) + s16v(word);
            for (i = d; i < 32; i++) { r[i] = rd32(c, ea); ea += 4; }
            break; }
        case 47: { /* stmw */
            int s = (word >> 21) & 31, a = (word >> 16) & 31, i;
            uint32_t ea = (a ? r[a] : 0) + s16v(word);
            for (i = s; i < 32; i++) { wr32(c, ea, r[i]); ea += 4; }
            break; }
        case 7: { /* mulli */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            r[d] = (uint32_t)(s32(r[a]) * s16v(word));
            break; }
        case 8: { /* subfic */
            int d = (word >> 21) & 31, a = (word >> 16) & 31;
            r[d] = (uint32_t)(s16v(word) - s32(r[a]));
            break; }
        case 10: { /* cmpli */
            int bf = (word >> 23) & 7, a = (word >> 16) & 31;
            setcr(c, bf, (int64_t)r[a], (int64_t)(word & 0xFFFF));
            break; }
        case 11: { /* cmpi */
            int bf = (word >> 23) & 7, a = (word >> 16) & 31;
            setcr(c, bf, s32(r[a]), s16v(word));
            break; }
        case 24: case 25: { /* ori / oris */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            r[a] = r[s] | ((word & 0xFFFF) << (op == 25 ? 16 : 0));
            break; }
        case 26: case 27: { /* xori / xoris */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            r[a] = r[s] ^ ((word & 0xFFFF) << (op == 27 ? 16 : 0));
            break; }
        case 28: case 29: { /* andi. / andis. */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            r[a] = r[s] & ((word & 0xFFFF) << (op == 29 ? 16 : 0));
            setcr(c, 0, s32(r[a]), 0);
            break; }
        case 20: case 21: case 23: { /* rlwimi / rlwinm / rlwnm */
            int s = (word >> 21) & 31, a = (word >> 16) & 31;
            int sh = (op != 23) ? ((word >> 11) & 31) : (r[(word >> 11) & 31] & 31);
            int mb = (word >> 6) & 31, me = (word >> 1) & 31;
            uint32_t val = sh ? ((r[s] << sh) | (r[s] >> (32 - sh))) : r[s];
            uint32_t mask = maskbits(mb, me);
            if (op == 20) r[a] = (val & mask) | (r[a] & ~mask);
            else r[a] = val & mask;
            if (word & 1) setcr(c, 0, s32(r[a]), 0);
            break; }
        case 18: { /* b / bl */
            int32_t li = word & 0x03FFFFFC;
            if (li & 0x02000000) li -= 0x04000000;
            if (word & 1) c->lr = nxt;
            nxt = (word & 2) ? (uint32_t)li : (pc + li);
            break; }
        case 16: { /* bc / bcl */
            int bo = (word >> 21) & 31, bi = (word >> 16) & 31;
            int32_t bd = word & 0xFFFC;
            int take;
            if (bd & 0x8000) bd -= 0x10000;
            take = cond(c, bo, bi);
            if (word & 1) c->lr = nxt;
            if (take) nxt = (word & 2) ? (uint32_t)bd : (pc + bd);
            break; }
        case 19: { /* bclr / bcctr / cror */
            int xo = (word >> 1) & 0x3FF;
            int bo = (word >> 21) & 31, bi = (word >> 16) & 31;
            if (xo == 16 || xo == 528) {
                int take = cond(c, bo, bi);
                uint32_t t = (xo == 16) ? c->lr : c->ctr;
                if (word & 1) c->lr = nxt;
                if (take) nxt = t;
            } else if (xo == 449) {          /* cror */
                int bt = (word >> 21) & 31, ba = (word >> 16) & 31,
                    bb = (word >> 11) & 31;
                int va = ((ba & 3) < 3) ? c->cr[ba >> 2][ba & 3] : 0;
                int vb = ((bb & 3) < 3) ? c->cr[bb >> 2][bb & 3] : 0;
                if ((bt & 3) < 3) c->cr[bt >> 2][bt & 3] = (va || vb) ? 1 : 0;
            } else { c->err_op = 19; c->err_xo = xo; return 3; }
            break; }
        case 31: {
            int xo = (word >> 1) & 0x3FF;
            int d = (word >> 21) & 31, a = (word >> 16) & 31, b = (word >> 11) & 31;
            int rc = word & 1, handled = 1;
            switch (xo) {
            case 266: case 10: r[d] = r[a] + r[b]; break;      /* add/addc */
            case 40:  case 8:  r[d] = r[b] - r[a]; break;      /* subf/subfc */
            case 235: r[d] = (uint32_t)(s32(r[a]) * s32(r[b])); break;
            case 75:  r[d] = (uint32_t)(((int64_t)s32(r[a]) * s32(r[b])) >> 32); break;
            case 11:  r[d] = (uint32_t)(((uint64_t)r[a] * r[b]) >> 32); break;
            case 491: { int32_t x = s32(r[a]), y = s32(r[b]);
                        r[d] = (y == 0 || (x == INT32_MIN && y == -1))
                             ? 0 : (uint32_t)(x / y); break; }
            case 459: r[d] = r[b] ? (r[a] / r[b]) : 0; break;
            case 444: r[a] = r[d] | r[b]; break;
            case 28:  r[a] = r[d] & r[b]; break;
            case 316: r[a] = r[d] ^ r[b]; break;
            case 476: r[a] = ~(r[d] & r[b]); break;
            case 124: r[a] = ~(r[d] | r[b]); break;
            case 104: r[d] = (uint32_t)(-s32(r[a])); break;
            case 922: r[a] = (uint32_t)(int32_t)(int16_t)(r[d] & 0xFFFF); break;
            case 954: r[a] = (uint32_t)(int32_t)(int8_t)(r[d] & 0xFF); break;
            case 24:  { int sh = r[b] & 63; r[a] = (sh > 31) ? 0 : (r[d] << sh); break; }
            case 536: { int sh = r[b] & 63; r[a] = (sh > 31) ? 0 : (r[d] >> sh); break; }
            case 792: { int sh = r[b] & 63; if (sh > 31) sh = 31;
                        r[a] = (uint32_t)(s32(r[d]) >> sh); break; }
            case 824: r[a] = (uint32_t)(s32(r[d]) >> b); break;
            case 0:   setcr(c, (word >> 23) & 7, s32(r[a]), s32(r[b])); break;
            case 32:  setcr(c, (word >> 23) & 7, (int64_t)r[a], (int64_t)r[b]); break;
            case 339: { int spr = ((word >> 16) & 31) | (((word >> 11) & 31) << 5);
                        r[d] = (spr == 8) ? c->lr : (spr == 9 ? c->ctr : c->xer);
                        break; }
            case 467: { int spr = ((word >> 16) & 31) | (((word >> 11) & 31) << 5);
                        if (spr == 8) c->lr = r[d];
                        else if (spr == 9) c->ctr = r[d];
                        else c->xer = r[d];
                        break; }
            case 23:  r[d] = rd32(c, (a ? r[a] : 0) + r[b]); break;
            case 279: r[d] = rd16(c, (a ? r[a] : 0) + r[b]); break;
            case 87:  r[d] = rd8(c, (a ? r[a] : 0) + r[b]); break;
            case 151: wr32(c, (a ? r[a] : 0) + r[b], r[d]); break;
            case 407: wr16(c, (a ? r[a] : 0) + r[b], r[d] & 0xFFFF); break;
            case 535: f[d] = rdf32(c, (a ? r[a] : 0) + r[b]); break;
            case 663: wrf32(c, (a ? r[a] : 0) + r[b], f[d]); break;
            case 598: break;                                  /* sync */
            default: handled = 0; break;
            }
            if (!handled) { c->err_op = 31; c->err_xo = xo; return 3; }
            if (rc) {
                switch (xo) {
                case 444: case 28: case 316: case 24: case 536:
                case 792: case 824: case 922:
                    setcr(c, 0, s32(r[a]), 0); break;
                case 266: case 40:
                    setcr(c, 0, s32(r[d]), 0); break;
                default: break;
                }
            }
            break; }
        case 59: case 63: {
            int d = (word >> 21) & 31, a = (word >> 16) & 31,
                b = (word >> 11) & 31, cc = (word >> 6) & 31;
            int xo = (word >> 1) & 0x1F, xo10 = (word >> 1) & 0x3FF;
            double v;
            int arith = 1;
            switch (xo) {
            case 21: v = f[a] + f[b]; break;
            case 20: v = f[a] - f[b]; break;
            case 25: v = f[a] * f[cc]; break;
            case 18: v = (f[b] != 0.0) ? (f[a] / f[b]) : 0.0; break;
            case 29: v = f[a] * f[cc] + f[b]; break;
            case 28: v = f[a] * f[cc] - f[b]; break;
            case 31: v = -(f[a] * f[cc] + f[b]); break;
            case 30: v = -(f[a] * f[cc] - f[b]); break;
            default: arith = 0; v = 0.0; break;
            }
            if (arith) {
                f[d] = (op == 59) ? single(v) : v;
            } else if (xo10 == 72) {  f[d] = f[b];
            } else if (xo10 == 40) {  f[d] = -f[b];
            } else if (xo10 == 264) { f[d] = fabs(f[b]);
            } else if (xo10 == 12) {  f[d] = single(f[b]);
            } else if (xo10 == 15) {  f[d] = int_pattern(to_int(f[b], 1));
            } else if (xo10 == 14) {  f[d] = int_pattern(to_int(f[b], 0));
            } else if (xo10 == 0 || xo10 == 32) {
                int bf = (word >> 23) & 7;
                c->cr[bf][0] = f[a] < f[b];
                c->cr[bf][1] = f[a] > f[b];
                c->cr[bf][2] = f[a] == f[b];
            } else { c->err_op = op; c->err_xo = xo10; return 3; }
            break; }
        default:
            c->err_op = op; c->err_xo = -1;
            return 3;
        }
        c->pc = nxt;
    }
}
