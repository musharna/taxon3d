#!/usr/bin/env Rscript
# scripts/paper/fig_aprime_power.R
# Power for Study A′'s criterion contrast against the number of VOTERS recruited.
# Voters, not ballots: each voter is assigned one criterion for a whole session, so the
# contrast is a between-voter comparison and extra ballots from the same person buy very little.
#
# Usage: Rscript scripts/paper/fig_aprime_power.R data/paper/aprime/power.json out.png
suppressPackageStartupMessages({
  library(ggplot2)
  library(jsonlite)
})
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))), "theme_taxon3d.R"))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("usage: fig_aprime_power.R <power.json> <out.png>")
d <- fromJSON(args[1], simplifyVector = FALSE)

rows <- do.call(rbind, lapply(d$curve, function(cv) {
  do.call(rbind, lapply(cv$points, function(p) data.frame(
    voters = p$voters,
    power = p$power,
    cost = p$cost_usd,
    gap_pts = 100 * cv$gap_marginal,
    stringsAsFactors = FALSE
  )))
}))
# Order the legend by effect size, largest first — the reader scans the cheapest study first.
rows$gap <- factor(sprintf("%.0f-point gap", rows$gap_pts),
                   levels = sprintf("%.0f-point gap", sort(unique(rows$gap_pts), decreasing = TRUE)))

# Cost axis is exact: Prolific is paid per participant at wave 2's measured rate.
usd_per_voter <- d$wave2_reference$cost_usd / d$wave2_reference$approved
breaks <- c(20, 60, 120, 240, 480, 640)
breaks <- breaks[breaks <= max(rows$voters)]

p <- ggplot(rows, aes(voters, power, colour = gap, group = gap)) +
  geom_hline(yintercept = 0.8, linetype = "dashed",
             colour = taxon3d_palette[["neutral"]]) +
  geom_line(linewidth = 0.7) +
  geom_point(size = 1.8) +
  scale_x_continuous(
    trans = "log2", breaks = breaks,
    sec.axis = sec_axis(~ . * usd_per_voter, name = "Prolific cost (USD, wave-2 rate)",
                        breaks = breaks * usd_per_voter,
                        labels = function(x) paste0("$", round(x)))
  ) +
  scale_y_continuous(limits = c(0, 1), labels = scales::percent_format(accuracy = 1)) +
  scale_colour_manual(values = unname(taxon3d_palette[c("novel", "semantic", "admitted")]),
                      name = NULL) +
  labs(
    title = "Study A′ is powered in voters, not ballots",
    subtitle = paste(
      "Criterion is assigned per voter for a whole session, so the visual-vs-botanical",
      "contrast is a between-voter comparison. Dashed line: 80% power.",
      "Voter heterogeneity set to a conservative sd = 0.8. Wave 2 bounds it at 0.5,",
      "which shifts every curve left.",
      sep = "\n"
    ),
    x = "Voters recruited (log scale)",
    y = "Power for the criterion contrast"
  ) +
  theme_taxon3d_xy()

ggsave(args[2], p, width = 7.2, height = 4.6, dpi = 200)
cat("wrote", args[2], "\n")
