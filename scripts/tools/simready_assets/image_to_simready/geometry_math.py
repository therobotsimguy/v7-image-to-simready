"""Cabinet layout grid for V7 stage_c — column/row centers in meters (carcass space)."""


class CabinetGrid:
    """
    Simple width × depth × height carcass split into columns (X) and stacked rows (Z).
    row_heights from stage_c is typically [door_h, drawer_h]: row 0 = upper doors,
    row 1 = lower drawers, measured from the bottom of the carcass (above legs).
    """

    def __init__(
        self,
        width: float,
        depth: float,
        height: float,
        columns: int,
        rows: int,
        panel_t: float,
        row_heights: list,
        leg_height: float,
    ):
        self.width = float(width)
        self.depth = float(depth)
        self.height = float(height)
        self.columns = max(1, int(columns))
        self.rows = max(1, int(rows))
        self.panel_t = float(panel_t)
        self.row_heights = [float(x) for x in row_heights] if row_heights else [self.height]
        self.leg_height = float(leg_height)

    def col_center(self, col_idx: int) -> float:
        n = self.columns
        col_idx = max(0, min(n - 1, int(col_idx)))
        cell = self.width / n
        return -self.width / 2.0 + cell * (col_idx + 0.5)

    def row_center(self, row_idx: int) -> float:
        """Z center of band ``row_idx`` from bottom of carcass (Z=0 at carcass floor)."""
        rh = self.row_heights
        row_idx = int(row_idx)
        if len(rh) == 1:
            return rh[0] / 2.0
        if len(rh) >= 2 and row_idx == 1:
            return rh[1] / 2.0
        if len(rh) >= 2 and row_idx == 0:
            return rh[1] + self.panel_t + rh[0] / 2.0
        z = 0.0
        for i, h in enumerate(rh):
            if i == row_idx:
                return z + h / 2.0
            z += h + self.panel_t
        return self.height / 2.0
