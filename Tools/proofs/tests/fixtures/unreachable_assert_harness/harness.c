#include "loop.c"

void harness(void)
{
    int a[4];
    int n;
    __CPROVER_assume(0 <= n && n <= 4);
    for (int i = 0; i < 4; i++)
        __CPROVER_assume(-1000 <= a[i] && a[i] <= 1000);
    int s = sum(a, n);
    if (n > 10) {
        __CPROVER_assert(s == 12345, "dead code: n <= 4");
    }
    __CPROVER_assert(-4000 <= s && s <= 4000, "sum bounded");
}
