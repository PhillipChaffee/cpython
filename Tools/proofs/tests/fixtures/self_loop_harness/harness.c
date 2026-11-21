#include "loop.c"

void harness(void)
{
    int a[4];
    int n;
    if (n < 0 || n > 4) for (;;);
    for (int i = 0; i < 4; i++)
        __CPROVER_assume(-1000 <= a[i] && a[i] <= 1000);
    int s = sum(a, n);
    __CPROVER_assert(-4000 <= s && s <= 4000, "the self-loop bounds n");
}
