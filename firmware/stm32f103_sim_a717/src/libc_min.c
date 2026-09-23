/* Minimal freestanding string/memory routines.
 *
 * The firmware links with -nostdlib, but GCC is free to emit calls to
 * memset/memcpy/memmove/memcmp (array initialisers, struct copies) and the
 * command parser uses strcmp/strlen.  These definitions stand in for libc.
 * They are compiled with -fno-tree-loop-distribute-patterns (see Makefile)
 * so GCC cannot turn their loops back into calls to themselves. */
#include <stddef.h>
#include <stdint.h>

void *memset(void *dst, int value, size_t len)
{
    uint8_t *d = dst;
    while (len--) *d++ = (uint8_t)value;
    return dst;
}

void *memcpy(void *dst, const void *src, size_t len)
{
    uint8_t *d = dst;
    const uint8_t *s = src;
    while (len--) *d++ = *s++;
    return dst;
}

void *memmove(void *dst, const void *src, size_t len)
{
    uint8_t *d = dst;
    const uint8_t *s = src;
    if (d < s) {
        while (len--) *d++ = *s++;
    } else {
        d += len; s += len;
        while (len--) *--d = *--s;
    }
    return dst;
}

int memcmp(const void *a, const void *b, size_t len)
{
    const uint8_t *x = a, *y = b;
    for (; len; len--, x++, y++)
        if (*x != *y) return *x - *y;
    return 0;
}

int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}

size_t strlen(const char *s)
{
    size_t n = 0;
    while (s[n]) n++;
    return n;
}
