#pragma once
static int
sub_from(int *x, int v)
__CPROVER_requires(__CPROVER_rw_ok(x, sizeof(int)))
__CPROVER_requires(0 <= v && v <= *x && *x <= 100)
__CPROVER_assigns(*x)
__CPROVER_ensures(*x == __CPROVER_old(*x) - v)
__CPROVER_ensures(__CPROVER_return_value == *x)
;
