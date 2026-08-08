#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 ABSOLUTE_INSTALL_PREFIX" >&2
    exit 2
fi

PREFIX=$1
if [[ "$PREFIX" != /* || "$PREFIX" == "/" ]]; then
    echo "install prefix must be a non-root absolute path" >&2
    exit 2
fi

for tool in git cmake make mpicxx curl tar; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "missing $tool; load compiler, OpenMPI, and CMake modules first" >&2
        exit 1
    fi
done

mkdir -p "$PREFIX/src" "$PREFIX/build" "$PREFIX/install" "$PREFIX/bin"

if [[ ! -d "$PREFIX/src/nektar/.git" ]]; then
    git clone --depth 1 --branch v5.10.0 https://gitlab.nektar.info/nektar/nektar.git "$PREFIX/src/nektar"
fi

GMSH_VERSION=4.13.1
GMSH_ARCHIVE="gmsh-${GMSH_VERSION}-Linux64.tgz"
if command -v gmsh >/dev/null 2>&1; then
    echo "using gmsh already available in PATH: $(command -v gmsh)"
elif [[ ! -x "$PREFIX/bin/gmsh" ]]; then
    curl -L --fail --retry 3 \
        "https://gmsh.info/bin/Linux/${GMSH_ARCHIVE}" \
        -o "$PREFIX/${GMSH_ARCHIVE}"
    tar --no-same-owner -xzf "$PREFIX/${GMSH_ARCHIVE}" -C "$PREFIX"
    ln -s "$PREFIX/gmsh-${GMSH_VERSION}-Linux64/bin/gmsh" "$PREFIX/bin/gmsh"
    "$PREFIX/bin/gmsh" --version
fi

cmake -S "$PREFIX/src/nektar" -B "$PREFIX/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX/install" \
    -DNEKTAR_USE_MPI=ON \
    -DNEKTAR_USE_HDF5=OFF \
    -DNEKTAR_USE_FFTW=OFF \
    -DNEKTAR_USE_SCOTCH=OFF \
    -DTHIRDPARTY_BUILD_SCOTCH=OFF \
    -DNEKTAR_USE_METIS=ON \
    -DTHIRDPARTY_BUILD_METIS=ON \
    -DTHIRDPARTY_BUILD_BOOST=ON \
    -DTHIRDPARTY_BUILD_ZLIB=ON \
    -DTHIRDPARTY_BUILD_TINYXML=ON \
    -DNEKTAR_BUILD_SOLVERS=ON \
    -DNEKTAR_SOLVER_COMPRESSIBLE_FLOW=ON \
    -DNEKTAR_BUILD_UTILITIES=ON \
    -DNEKTAR_BUILD_DEMOS=OFF \
    -DNEKTAR_BUILD_UNIT_TESTS=OFF \
    -DNEKTAR_BUILD_TESTS=OFF \
    -DNEKTAR_BUILD_PERFORMANCE_TESTS=OFF \
    -DNEKTAR_BUILD_PYTHON=OFF

CACHE_FILE="$PREFIX/build/CMakeCache.txt"
for expected in \
    'NEKTAR_USE_SCOTCH:BOOL=OFF' \
    'NEKTAR_USE_METIS:BOOL=ON'; do
    if ! grep -qx "$expected" "$CACHE_FILE"; then
        echo "unexpected Nektar++ partitioner configuration: $expected" >&2
        exit 1
    fi
done

cmake --build "$PREFIX/build" --parallel "${NEKTAR_BUILD_JOBS:-8}"
cmake --install "$PREFIX/build"

ENV_FILE="$PREFIX/nektar_env.sh"
{
    printf 'export PATH="%s/install/bin:%s/bin:$PATH"\n' "$PREFIX" "$PREFIX"
    printf 'export LD_LIBRARY_PATH="%s/install/lib:${LD_LIBRARY_PATH:-}"\n' "$PREFIX"
} > "$ENV_FILE"

echo "installation complete"
echo "export NEKTAR_ENV_FILE=$ENV_FILE"
echo "source $ENV_FILE"
echo "CompressibleFlowSolver --version"
