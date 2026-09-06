# scripts/paper/fig_cprime_ppv.R
# Usage: Rscript scripts/paper/fig_cprime_ppv.R results.json out.png
source(file.path(dirname(sub("--file=", "", grep("--file=", commandArgs(), value = TRUE))), "theme_taxon3d.R"))
suppressPackageStartupMessages(library(jsonlite))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("usage: fig_cprime_ppv.R results.json out.png")
if (!file.exists(args[[1]])) stop("no such file: ", args[[1]])
res <- fromJSON(args[[1]], simplifyVector = FALSE)

order <- c("struct_degenerate_bbox", "struct_empty", "novel_multiple", "novel_not_the_organism",
           "novel_sub_part", "sem_also_completeness", "sem_only_other", "admitted")
rows <- lapply(order, function(s) {
  d <- res$per_stratum[[s]]
  if (is.null(d) || is.null(d$ppv)) return(NULL)
  data.frame(stratum = s, est = d$ppv, lo = d$ppv_ci[[1]], hi = d$ppv_ci[[2]], n = d$labelled,
             group = if (startsWith(s, "struct")) "structural" else if (startsWith(s, "novel")) "novel"
                     else if (s == "admitted") "admitted" else "semantic")
})
df <- do.call(rbind, rows)
df$stratum <- factor(df$stratum, levels = rev(order))
df$label <- ifelse(df$stratum == "admitted", "share inadmissible (false-negative rate)", "PPV of the rejection")

p <- ggplot(df, aes(x = est, y = stratum, colour = group)) +
  geom_vline(xintercept = 0.8, linetype = "dashed", colour = taxon3d_palette[["neutral"]]) +
  geom_vline(xintercept = 0.1, linetype = "dotted", colour = taxon3d_palette[["neutral"]]) +
  geom_errorbar(aes(xmin = lo, xmax = hi), orientation = "y", width = 0.25) +
  geom_point(size = 2.6) +
  geom_text(aes(label = paste0("n=", n)), nudge_y = 0.32, size = 3, colour = taxon3d_palette[["neutral"]]) +
  scale_colour_manual(values = taxon3d_palette, guide = "none") +
  scale_x_continuous(limits = c(0, 1), breaks = seq(0, 1, 0.2)) +
  labs(x = "Proportion judged inadmissible by humans (Wilson 95% CI)", y = NULL,
       title = "Study C′: does the gate reject what humans reject?",
       subtitle = paste("Dashed: go threshold PPV 0.8 for rejected strata.",
                        "Dotted: ceiling 0.1 for the admitted cell.", sep = "\n")) +
  theme_taxon3d()

ggsave(args[[2]], p, width = 7.5, height = 4.5, dpi = 200)
