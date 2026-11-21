/* Enforces add_into()'s contract: its requires are assumed, its ensures
 * and assigns checked. */
#include "contract_src.c"
#include "add_into.h"

void harness(void)
{
    int x;
    int v;
    add_into(&x, v);
}
