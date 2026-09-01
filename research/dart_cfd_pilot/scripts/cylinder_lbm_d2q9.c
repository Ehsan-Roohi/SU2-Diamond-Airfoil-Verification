#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>

#ifdef _OPENMP
#include <omp.h>
#endif

static const int cx[9] = {0, 1, 0, -1, 0, 1, -1, -1, 1};
static const int cy[9] = {0, 0, 1, 0, -1, 1, 1, -1, -1};
static const int opposite[9] = {0, 3, 4, 1, 2, 7, 8, 5, 6};
static const double weight[9] = {
    4.0 / 9.0,
    1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0, 1.0 / 9.0,
    1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0, 1.0 / 36.0,
};
static size_t cell_index(int x, int y, int ny) {
    return (size_t)x * (size_t)ny + (size_t)y;
}

static size_t population_index(int q, size_t cell, size_t cells) {
    return (size_t)q * cells + cell;
}

static double equilibrium(int q, double rho, double ux, double uy) {
    const double cu = (double)cx[q] * ux + (double)cy[q] * uy;
    const double uu = ux * ux + uy * uy;
    return weight[q] * rho * (1.0 + 3.0 * cu + 4.5 * cu * cu - 1.5 * uu);
}

static void macroscopic(const double *f, size_t cell, size_t cells,
                        double *rho, double *ux, double *uy) {
    double density = 0.0;
    double momentum_x = 0.0;
    double momentum_y = 0.0;
    for (int q = 0; q < 9; ++q) {
        const double value = f[population_index(q, cell, cells)];
        density += value;
        momentum_x += value * (double)cx[q];
        momentum_y += value * (double)cy[q];
    }
    *rho = density;
    *ux = momentum_x / density;
    *uy = momentum_y / density;
}

static int write_snapshot(const char *directory, int step, int nx, int ny,
                          const double *f, const uint8_t *solid, size_t cells) {
    char path[4096];
    const int written = snprintf(path, sizeof(path), "%s/snapshot_%07d.bin", directory, step);
    if (written < 0 || (size_t)written >= sizeof(path)) {
        fprintf(stderr, "snapshot path is too long\n");
        return 1;
    }
    FILE *stream = fopen(path, "wb");
    if (stream == NULL) {
        fprintf(stderr, "cannot open %s: %s\n", path, strerror(errno));
        return 1;
    }
    const uint32_t header[3] = {(uint32_t)nx, (uint32_t)ny, (uint32_t)step};
    if (fwrite(header, sizeof(uint32_t), 3, stream) != 3) {
        fclose(stream);
        return 1;
    }
    float *buffer = (float *)malloc(cells * sizeof(float));
    if (buffer == NULL) {
        fclose(stream);
        return 1;
    }
    for (int field = 0; field < 3; ++field) {
        for (size_t cell = 0; cell < cells; ++cell) {
            if (solid[cell]) {
                buffer[cell] = (field == 0) ? 1.0f : 0.0f;
                continue;
            }
            double rho, ux, uy;
            macroscopic(f, cell, cells, &rho, &ux, &uy);
            buffer[cell] = (float)((field == 0) ? rho : ((field == 1) ? ux : uy));
        }
        if (fwrite(buffer, sizeof(float), cells, stream) != cells) {
            free(buffer);
            fclose(stream);
            return 1;
        }
    }
    free(buffer);
    if (fclose(stream) != 0) {
        return 1;
    }
    return 0;
}

static void usage(const char *program) {
    fprintf(stderr,
            "usage: %s NX NY DIAMETER RE U STEPS SAMPLE_START SNAPSHOT_STRIDE "
            "MONITOR_STRIDE OUTPUT_DIR [circle|square]\n",
            program);
}

int main(int argc, char **argv) {
    if (argc != 11 && argc != 12) {
        usage(argv[0]);
        return 2;
    }
    const int nx = atoi(argv[1]);
    const int ny = atoi(argv[2]);
    const double diameter = atof(argv[3]);
    const double reynolds = atof(argv[4]);
    const double inlet_u = atof(argv[5]);
    const int steps = atoi(argv[6]);
    const int sample_start = atoi(argv[7]);
    const int snapshot_stride = atoi(argv[8]);
    const int monitor_stride = atoi(argv[9]);
    const char *output_dir = argv[10];
    const char *obstacle_shape = (argc == 12) ? argv[11] : "circle";
    const int square_obstacle = strcmp(obstacle_shape, "square") == 0;
    if (!square_obstacle && strcmp(obstacle_shape, "circle") != 0) {
        fprintf(stderr, "unsupported obstacle shape: %s\n", obstacle_shape);
        usage(argv[0]);
        return 2;
    }
    if (nx < 64 || ny < 48 || diameter < 6.0 || reynolds <= 0.0 || inlet_u <= 0.0 ||
        inlet_u >= 0.15 || steps < 1 || sample_start < 0 || snapshot_stride < 1 ||
        monitor_stride < 1) {
        usage(argv[0]);
        return 2;
    }
    if (mkdir(output_dir, 0770) != 0 && errno != EEXIST) {
        fprintf(stderr, "cannot create %s: %s\n", output_dir, strerror(errno));
        return 2;
    }

    const size_t cells = (size_t)nx * (size_t)ny;
    const size_t populations = (size_t)9 * cells;
    double *f = (double *)calloc(populations, sizeof(double));
    double *post = (double *)calloc(populations, sizeof(double));
    double *next = (double *)calloc(populations, sizeof(double));
    uint8_t *solid = (uint8_t *)calloc(cells, sizeof(uint8_t));
    if (f == NULL || post == NULL || next == NULL || solid == NULL) {
        fprintf(stderr, "allocation failure\n");
        free(f); free(post); free(next); free(solid);
        return 2;
    }

    const double radius = 0.5 * diameter;
    const double cylinder_x = 5.0 * diameter;
    const double cylinder_y = 0.5 * (double)(ny - 1);
    if (cylinder_x + radius + 4.0 >= (double)nx || diameter * 4.0 >= (double)ny) {
        fprintf(stderr, "domain is too small for the requested cylinder\n");
        free(f); free(post); free(next); free(solid);
        return 2;
    }
    for (int x = 0; x < nx; ++x) {
        for (int y = 0; y < ny; ++y) {
            const double dx = (double)x - cylinder_x;
            const double dy = (double)y - cylinder_y;
            solid[cell_index(x, y, ny)] = (uint8_t)(
                square_obstacle
                    ? (fabs(dx) <= radius && fabs(dy) <= radius)
                    : (dx * dx + dy * dy <= radius * radius)
            );
        }
    }

    const double viscosity = inlet_u * diameter / reynolds;
    const double tau = 0.5 + 3.0 * viscosity;
    const double relaxation = 1.0 / tau;
    if (tau <= 0.5001) {
        fprintf(stderr, "unstable relaxation time tau=%g\n", tau);
        free(f); free(post); free(next); free(solid);
        return 2;
    }
    for (int x = 0; x < nx; ++x) {
        for (int y = 0; y < ny; ++y) {
            const size_t cell = cell_index(x, y, ny);
            const double dx = ((double)x - cylinder_x) / diameter;
            const double dy = ((double)y - cylinder_y) / diameter;
            const double envelope = exp(-0.5 * ((dx - 1.25) * (dx - 1.25) + 0.30 * dy * dy));
            const double uy = 0.02 * inlet_u * envelope;
            for (int q = 0; q < 9; ++q) {
                f[population_index(q, cell, cells)] = equilibrium(q, 1.0, inlet_u, uy);
            }
        }
    }

    char monitor_path[4096];
    if (snprintf(monitor_path, sizeof(monitor_path), "%s/cylinder_monitor.csv", output_dir) < 0) {
        free(f); free(post); free(next); free(solid);
        return 2;
    }
    FILE *monitor = fopen(monitor_path, "w");
    if (monitor == NULL) {
        fprintf(stderr, "cannot open monitor output: %s\n", strerror(errno));
        free(f); free(post); free(next); free(solid);
        return 2;
    }
    if (setvbuf(monitor, NULL, _IONBF, 0) != 0) {
        fprintf(stderr, "cannot make monitor output unbuffered\n");
        fclose(monitor);
        free(f); free(post); free(next); free(solid);
        return 2;
    }
    fprintf(monitor, "step,probe_u,probe_v,rho_min,rho_max\n");
    const int probe_x = (int)lround(cylinder_x + 4.0 * diameter);
    const int probe_y = (int)lround(cylinder_y);

    printf("LBM_CYLINDER_NX=%d\nLBM_CYLINDER_NY=%d\n", nx, ny);
    printf("LBM_CYLINDER_RE=%.9g\nLBM_CYLINDER_U=%.9g\n", reynolds, inlet_u);
    printf("LBM_CYLINDER_DIAMETER=%.9g\nLBM_CYLINDER_TAU=%.12g\n", diameter, tau);
    printf("LBM_OBSTACLE_SHAPE=%s\n", obstacle_shape);
    fflush(stdout);

    for (int step = 0; step <= steps; ++step) {
        if (step % monitor_stride == 0) {
            double probe_rho, probe_u, probe_v;
            macroscopic(f, cell_index(probe_x, probe_y, ny), cells,
                        &probe_rho, &probe_u, &probe_v);
            double rho_min = 1.0e300;
            double rho_max = -1.0e300;
#pragma omp parallel for reduction(min:rho_min) reduction(max:rho_max) schedule(static)
            for (size_t cell = 0; cell < cells; ++cell) {
                if (solid[cell]) continue;
                double rho = 0.0;
                for (int q = 0; q < 9; ++q) {
                    rho += f[population_index(q, cell, cells)];
                }
                if (rho < rho_min) rho_min = rho;
                if (rho > rho_max) rho_max = rho;
            }
            if (fprintf(monitor, "%d,%.12e,%.12e,%.12e,%.12e\n",
                        step, probe_u, probe_v, rho_min, rho_max) < 0) {
                fprintf(stderr, "failed to write monitor output at step %d\n", step);
                fclose(monitor);
                free(f); free(post); free(next); free(solid);
                return 3;
            }
            if (!isfinite(rho_min) || !isfinite(rho_max) || rho_min <= 0.0 || rho_max > 2.0) {
                fprintf(stderr, "LBM diverged at step %d: rho=[%g,%g]\n", step, rho_min, rho_max);
                fclose(monitor);
                free(f); free(post); free(next); free(solid);
                return 4;
            }
        }
        if (step >= sample_start && (step - sample_start) % snapshot_stride == 0) {
            if (write_snapshot(output_dir, step, nx, ny, f, solid, cells) != 0) {
                fprintf(stderr, "failed to write snapshot at step %d\n", step);
                fclose(monitor);
                free(f); free(post); free(next); free(solid);
                return 3;
            }
        }
        if (step == steps) break;

#pragma omp parallel for schedule(static)
        for (size_t cell = 0; cell < cells; ++cell) {
            if (solid[cell]) continue;
            double rho, ux, uy;
            macroscopic(f, cell, cells, &rho, &ux, &uy);
            for (int q = 0; q < 9; ++q) {
                const size_t index = population_index(q, cell, cells);
                const double feq = equilibrium(q, rho, ux, uy);
                post[index] = f[index] - relaxation * (f[index] - feq);
            }
        }

#pragma omp parallel for collapse(2) schedule(static)
        for (int x = 1; x < nx - 1; ++x) {
            for (int y = 0; y < ny; ++y) {
                const size_t cell = cell_index(x, y, ny);
                if (solid[cell]) continue;
                for (int q = 0; q < 9; ++q) {
                    const int source_x = x - cx[q];
                    int source_y = y - cy[q];
                    if (source_y < 0) source_y += ny;
                    if (source_y >= ny) source_y -= ny;
                    const size_t source = cell_index(source_x, source_y, ny);
                    const size_t destination_index = population_index(q, cell, cells);
                    if (solid[source]) {
                        next[destination_index] = post[population_index(opposite[q], cell, cells)];
                    } else {
                        next[destination_index] = post[population_index(q, source, cells)];
                    }
                }
            }
        }

#pragma omp parallel for schedule(static)
        for (int y = 0; y < ny; ++y) {
            const size_t inlet = cell_index(0, y, ny);
            const size_t outlet = cell_index(nx - 1, y, ny);
            for (int boundary = 0; boundary < 2; ++boundary) {
                const int x = (boundary == 0) ? 0 : nx - 1;
                const size_t cell = (boundary == 0) ? inlet : outlet;
                for (int q = 0; q < 9; ++q) {
                    const int source_x = x - cx[q];
                    if (source_x < 0 || source_x >= nx) continue;
                    int source_y = y - cy[q];
                    if (source_y < 0) source_y += ny;
                    if (source_y >= ny) source_y -= ny;
                    const size_t source = cell_index(source_x, source_y, ny);
                    next[population_index(q, cell, cells)] = post[population_index(q, source, cells)];
                }
            }

            const double f0 = next[population_index(0, inlet, cells)];
            const double f2 = next[population_index(2, inlet, cells)];
            const double f4 = next[population_index(4, inlet, cells)];
            const double f3 = next[population_index(3, inlet, cells)];
            const double f6 = next[population_index(6, inlet, cells)];
            const double f7 = next[population_index(7, inlet, cells)];
            const double inlet_rho = (f0 + f2 + f4 + 2.0 * (f3 + f6 + f7)) /
                                     (1.0 - inlet_u);
            next[population_index(1, inlet, cells)] = f3 + (2.0 / 3.0) * inlet_rho * inlet_u;
            next[population_index(5, inlet, cells)] = f7 + 0.5 * (f4 - f2) +
                                                       (1.0 / 6.0) * inlet_rho * inlet_u;
            next[population_index(8, inlet, cells)] = f6 + 0.5 * (f2 - f4) +
                                                       (1.0 / 6.0) * inlet_rho * inlet_u;

            const size_t outlet_interior = cell_index(nx - 2, y, ny);
            for (int q = 0; q < 9; ++q) {
                next[population_index(q, outlet, cells)] =
                    next[population_index(q, outlet_interior, cells)];
            }
        }

        double *temporary = f;
        f = next;
        next = temporary;
    }

    if (fclose(monitor) != 0) {
        fprintf(stderr, "failed to close monitor output: %s\n", strerror(errno));
        free(f); free(post); free(next); free(solid);
        return 3;
    }
    printf("LBM_CYLINDER_STATUS=completed\n");
    printf("LBM_CYLINDER_MONITOR=%s\n", monitor_path);
    fflush(stdout);
    free(f); free(post); free(next); free(solid);
    return 0;
}
