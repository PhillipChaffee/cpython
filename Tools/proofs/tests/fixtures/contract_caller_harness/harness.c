/* add_three() with each call to add_into() replaced by its contract. */
#include "contract_src.c"
#include "add_into.h"

void harness(void)
{
    int x;
    __CPROVER_assume(0 <= x && x <= 50);
    int x0 = x;
    int r = add_three(&x);
    __CPROVER_assert(r == x0 + 3 && x == r, "add_three adds 3");
}
