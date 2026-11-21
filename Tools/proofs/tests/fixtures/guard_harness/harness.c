#include "guard.c"

void harness(void)
{
    int v;
    /* require() is not the target, and it may not return. */
    require(v >= 0);
    int r = clamp(v);
    __CPROVER_assert(0 <= r && r <= 10, "clamped");
}
