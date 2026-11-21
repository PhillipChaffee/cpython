#include "loop.c"
#include "../shared_helper/helper.h"

void harness(void)
{
    int a[4];
    int n;
    __CPROVER_assume(0 <= n && n <= 4);
    for (int i = 0; i < 4; i++)
        __CPROVER_assume(-1000 <= a[i] && a[i] <= 1000);
    int s = sum(a, n);
    shared_check(s);
    __CPROVER_assert(-4000 <= s && s <= 4000, "sum bounded");
}
