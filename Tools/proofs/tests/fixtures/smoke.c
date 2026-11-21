#include <limits.h>

int f(int x) { if (x == INT_MAX) return x; return x + 1; }
int g(int x) { return x + 1; }
