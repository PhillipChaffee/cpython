#pragma once
static int
add_into(int *x, int v)
__CPROVER_requires(__CPROVER_rw_ok(x, sizeof(int)))
__CPROVER_requires(0 <= *x && *x <= 100 && 0 <= v && v <= 100)
__CPROVER_assigns(*x)
__CPROVER_ensures(*x == __CPROVER_old(*x) + v)
__CPROVER_ensures(__CPROVER_return_value == *x)
;
