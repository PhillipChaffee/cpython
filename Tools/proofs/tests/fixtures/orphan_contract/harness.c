/* sub_three() with sub_from() replaced by a contract no harness enforces. */
#include "contract_src.c"
#include "orphan.h"

void harness(void)
{
    int x;
    __CPROVER_assume(3 <= x && x <= 50);
    int r = sub_three(&x);
    __CPROVER_assert(r == x, "sub_three returns the new value");
}
