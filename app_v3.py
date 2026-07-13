import numpy as np
import matplotlib.pyplot as plt
import gudhi

R = 0.60          # radio de las bolas
OUT = "complejos_10puntos_gudhi.png"

# --- nube: 10 puntos aleatorios ---
rng = np.random.default_rng(7)
P = rng.uniform(-1, 1, (10, 2))
n = len(P)

# ========================= Crear complejos con Gudhi =========================
# Rips: arista si d <= 2R (las bolas de radio R se solapan). Filtración = longitud de arista.
rips = gudhi.RipsComplex(points=P, max_edge_length=2 * R)
rips_st = rips.create_simplex_tree(max_dimension=2)

# Alpha: filtración = circunradio AL CUADRADO. Para radio R -> max_alpha_square = R**2.
# (create_simplex_tree de AlphaComplex NO usa max_dimension; en 2D ya llega a triángulos.)
alpha = gudhi.AlphaComplex(points=P)
alpha_st = alpha.create_simplex_tree(max_alpha_square=R ** 2)

# Alpha reindexa los puntos internamente: recuperamos las coordenadas en su orden.
alpha_coords = np.array([alpha.get_point(i) for i in range(n)])


def get_simplices_by_dim(st, dim):
    """Símplices de dimensión exacta dim."""
    return [s[0] for s in st.get_skeleton(dim) if len(s[0]) == dim + 1]


rips_edges = get_simplices_by_dim(rips_st, 1)
rips_tris = get_simplices_by_dim(rips_st, 2)
alpha_edges = get_simplices_by_dim(alpha_st, 1)
alpha_tris = get_simplices_by_dim(alpha_st, 2)

# --- Čech con numpy (gudhi.CechComplex no existe antes de GUDHI 3.5) ---
# Aristas de Čech(R) = aristas de Rips (ambas: dist <= 2R).
# Triángulo en Čech(R)  <=>  radio de la bola envolvente mínima <= R.
def miniball_radius(tri):
    """Radio de la bola envolvente mínima de 3 puntos en 2D."""
    A, B, C = P[tri[0]], P[tri[1]], P[tri[2]]
    best = np.inf
    # ¿basta con el diámetro de algún par? (triángulo no agudo)
    for a, b, c in ((A, B, C), (A, C, B), (B, C, A)):
        ctr = 0.5 * (a + b)
        r = 0.5 * np.linalg.norm(a - b)
        if np.linalg.norm(c - ctr) <= r + 1e-12:
            best = min(best, r)
    if np.isfinite(best):
        return best
    # triángulo agudo -> circunradio
    d = 2 * (A[0] * (B[1] - C[1]) + B[0] * (C[1] - A[1]) + C[0] * (A[1] - B[1]))
    if abs(d) < 1e-12:
        return np.inf
    a2, b2, c2 = (A ** 2).sum(), (B ** 2).sum(), (C ** 2).sum()
    ux = (a2 * (B[1] - C[1]) + b2 * (C[1] - A[1]) + c2 * (A[1] - B[1])) / d
    uy = (a2 * (C[0] - B[0]) + b2 * (A[0] - C[0]) + c2 * (B[0] - A[0])) / d
    return float(np.linalg.norm(A - np.array([ux, uy])))

cech_edges = rips_edges
cech_tris = [t for t in rips_tris if miniball_radius(t) <= R]

# ========================= Visualización =========================
def balls(ax, coords):
    for p in coords:
        ax.add_patch(plt.Circle(p, R, color="#d6e6f7", alpha=0.55, ec="none", zorder=0))


def draw(ax, coords, es, ts, title, show_balls=True):
    if show_balls:
        balls(ax, coords)
    for t in ts:                       # triángulos (2-símplices)
        poly = coords[list(t)]
        ax.fill(poly[:, 0], poly[:, 1], color="#1f4e9c", alpha=0.35, zorder=1)
    for e in es:                       # aristas (1-símplices)
        ax.plot(coords[list(e), 0], coords[list(e), 1], color="#1f4e9c", lw=1.6, zorder=2)
    ax.scatter(coords[:, 0], coords[:, 1], s=55, color="black",  # vértices
               edgecolors="k", linewidths=0.4, zorder=4)
    ax.set_title(title, fontsize=12)
    ax.set_aspect("equal")
    ax.axis("off")


fig, ax = plt.subplots(1, 3, figsize=(12, 4.3))
draw(ax[0], P, cech_edges, cech_tris, f"Čech  (r={R})")
draw(ax[1], P, rips_edges, rips_tris, f"Rips  (bolas radio {R})")
draw(ax[2], alpha_coords, alpha_edges, alpha_tris, f"Alpha  (r={R})")
fig.suptitle("Los tres complejos sobre la misma nube de 10 puntos "
             "(azul claro = bolas de radio r)", fontsize=14)
fig.tight_layout()
fig.savefig(OUT, dpi=150, bbox_inches="tight")
print("Figura guardada en", OUT)

print("\nResumen:")
print(f"  Čech:  {len(cech_edges)} aristas, {len(cech_tris)} triángulos")
print(f"  Rips:  {len(rips_edges)} aristas, {len(rips_tris)} triángulos")
print(f"  Alpha: {len(alpha_edges)} aristas, {len(alpha_tris)} triángulos")

# Chequeo de consistencia: los triángulos de Čech(R) son un subconjunto de los de Rips(R).
assert all(t in rips_tris for t in cech_tris), "Čech ⊆ Rips debería cumplirse"
print("  OK: aristas Čech = aristas Rips, y triángulos Čech ⊆ Rips.")

plt.show()
