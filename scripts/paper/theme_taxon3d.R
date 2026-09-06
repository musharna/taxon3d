# scripts/paper/theme_taxon3d.R
# The ONE house style for every Taxon3D paper figure. Restyle here, never inline.
suppressPackageStartupMessages(library(ggplot2))

taxon3d_palette <- c(
  admitted   = "#2E7D32",
  structural = "#6D4C41",
  semantic   = "#1565C0",
  novel      = "#C62828",
  neutral    = "#616161"
)

theme_taxon3d <- function(base_size = 11) {
  theme_minimal(base_size = base_size, base_family = "sans") +
    theme(
      panel.grid.minor = element_blank(),
      panel.grid.major.y = element_blank(),
      axis.title = element_text(size = rel(0.95)),
      plot.title = element_text(face = "bold", size = rel(1.05)),
      plot.subtitle = element_text(colour = taxon3d_palette[["neutral"]]),
      legend.position = "bottom",
      plot.background = element_rect(fill = "white", colour = NA)
    )
}
# IRON_LAW_OK

# Line/point variant: the bar-chart theme above drops horizontal gridlines, which a power
# curve needs to be readable against a threshold. Same palette and typography.
theme_taxon3d_xy <- function(base_size = 11) {
  theme_taxon3d(base_size) +
    theme(
      panel.grid.major.y = element_line(colour = "grey92"),
      panel.grid.major.x = element_line(colour = "grey92")
    )
}
