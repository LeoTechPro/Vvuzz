# Third-party notices

This directory includes upstream license and notice files for the Go toolchain
and modules in the pinned go.mod/go.sum dependency graph. They are distributed
with the installers and Linux archive. The original Vvuzz code uses MIT; these
dependencies retain their own terms.

The first extraction used google/go-licenses v1.6.0. Its classifier did not
recognize modernc.org/mathutil's standard BSD text; that module's LICENSE was
inspected directly and included. Root-level LICENSE, COPYING, NOTICE, AUTHORS
and CONTRIBUTORS files for the remaining pinned modules were included directly
from their verified Go module cache entries. Native Linux GTK and Ayatana
AppIndicator libraries are system dependencies, dynamically linked, and are
not bundled in the package. Their upstream licenses apply separately.
