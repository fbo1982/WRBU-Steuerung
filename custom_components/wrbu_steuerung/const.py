"""WRBU constants."""
DOMAIN = "wrbu_steuerung"
RETRY_SECONDS = 30
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni",
          "Juli", "August", "September", "Oktober", "November", "Dezember"]
PROFILE_NAMES = {
    "sungrow_sh": "Sungrow SH – direkter WR-Zugriff",
    "sungrow_sh_with_ihm": "Sungrow SH mit iHomeManager – Zugriff direkt auf WR",
    "sungrow_ihm_native": "Über iHomeManager selbst – Backup-SOC nicht unterstützt",
}
