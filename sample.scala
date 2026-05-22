import chisel3._

// 1. Simple configuration bundle with a 64-bit ID field
class tSAMPLE_CORE_CONFIG extends Bundle {
  val enable        = Bool()
  val mode          = UInt(3.W)
  val core_id       = UInt(64.W) // >32b field (Should be split)
  val timeout_ticks = UInt(16.W)
  val debug_en      = Bool()
}

// 2. Transmit-centric bundle featuring multiple wide data and mask fields
class tSAMPLE_TX_DATA extends Bundle {
  val tx_ready      = Bool()
  val packet_type   = UInt(4.W)
  val tx_data_lo    = UInt(64.W) // >32b field (Should be split)
  val tx_data_hi    = UInt(64.W) // >32b field (Should be split)
  val byte_mask     = UInt(16.W)
  val flush_toggle  = Bool()
}

// 3. Status/Telemetry bundle capturing massive counter accumulators
class tSAMPLE_METRICS extends Bundle {
  val cycle_count   = UInt(64.W) // >32b field (Should be split)
  val error_count   = UInt(32.W) // Exactly 32b
  val overflow_flag = Bool()
  val retry_count   = UInt(8.W)
  val valid_strobe  = Bool()
}

// 4. Cryptographic/Security-centric bundle with a massive 128-bit key field
class tSAMPLE_SECURITY_KEY extends Bundle {
  val key_valid     = Bool()
  val crypto_algo   = UInt(3.W)
  val secret_key    = UInt(128.W) // >32b field (Should split across 4 registers)
  val key_revocation= Bool()
  val privilege_lvl = UInt(2.W)
}
