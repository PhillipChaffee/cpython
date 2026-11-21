#include "weak.c"

void harness(void)
{
    int a, b;
    __CPROVER_assume(-100 <= a && a <= 100);
    __CPROVER_assume(-100 <= b && b <= 100);
    int r = clamp_sum(a, b);
    __CPROVER_assert(r >= 0, "result is not negative");
}
