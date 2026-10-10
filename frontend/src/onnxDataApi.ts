export const ONNX_DATA_STATUSES = [
  'NOT_STARTED',
  'PREFLIGHT_PASS',
  'PREFLIGHT_BLOCKED',
  'SNAPSHOT_AUDITED',
  'DQ_BLOCKED',
  'WINDOWS_BLOCKED',
  'WINDOWS_VALIDATED',
  'DATA_READY',
  'SYNTHETIC_TEST_EVIDENCE',
  'RECOVERY_REQUIRED',
] as const

export type OnnxDataStatus = typeof ONNX_DATA_STATUSES[number]

export interface DataIssue {
  code: string
  message: string
  severity: string
}

export interface DataQualityReport {
  status: 'PASS' | 'BLOCKED'
  schema_status: string
  row_count: number
  symbol: string | null
  timeframe: string | null
  period_enum: number | null
  timestamp_timezone: string
  timezone_provenance: string | null
  timestamp_min: string | null
  timestamp_max: string | null
  identical_duplicate_rows: number
  conflicting_duplicate_identities: number
  timestamp_discontinuity_status: string
  broker_reconciliation_status: string
  issues: DataIssue[]
}

export interface OnnxDataSnapshot {
  snapshot_id: string
  dataset_id: string
  sha256: string
  parent_snapshot_id: string | null
  filename: string
  source_path_sha256: string
  source_fingerprint: { file_id: string; size_bytes: number; modified_ns: string; created_ns: string }
  size_bytes: number
  row_count: number
  schema_id: string
  schema_version: string
  strategy_contract: string
  feature_contract: string
  ea_source_sha256: string
  ea_manifest_sha256: string
  symbol: string | null
  timeframe: string | null
  period_enum: number | null
  timestamp_timezone: string
  timezone_provenance: string | null
  timestamp_min: string | null
  timestamp_max: string | null
  timestamp_discontinuity_status: string
  broker_reconciliation_status: string
  dq_status: 'PASS' | 'BLOCKED'
  dq: DataQualityReport
  correction: Record<string, unknown> | null
  evidence_class: 'OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI' | 'SYNTHETIC_TEST_EVIDENCE'
  created_utc: string
}

export interface OnnxDataWorkspace {
  contract_version: '2.0'
  source: 'BACKEND_ONNX_02_DATA_API'
  status: OnnxDataStatus
  implementation_status: 'IMPLEMENTED'
  real_data_readiness: 'NOT_PROVEN' | 'DATA_READY'
  latest_snapshot: OnnxDataSnapshot | null
  window_config: Record<string, unknown> | null
  readiness_evidence: Record<string, unknown> | null
  snapshots: OnnxDataSnapshot[]
  scientific_execution: 'NOT_IMPLEMENTED'
  first_blocker: string | null
}

export interface OnnxDataPreflight {
  contract_version: '2.0'
  source: 'BACKEND_ONNX_02_DATA_API'
  status: 'PREFLIGHT_PASS' | 'PREFLIGHT_BLOCKED'
  snapshot_permitted: boolean
  source_identity: {
    filename: string
    path_sha256: string
    sha256: string
    fingerprint: { file_id: string; size_bytes: number; modified_ns: string; created_ns: string }
    size_bytes: number
  }
  authority: Record<string, string>
  data_quality: DataQualityReport
  first_blocker: string | null
  evidence_class: OnnxDataSnapshot['evidence_class']
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function contractError(detail: string): Error {
  return new Error(`ONNX data response violates the v2 read contract: ${detail}`)
}

function exactRecord(value: unknown, keys: readonly string[], label: string): asserts value is Record<string, unknown> {
  if (!isRecord(value)) throw contractError(`${label} must be an object.`)
  const actual = Object.keys(value)
  if (actual.length !== keys.length || actual.some((key) => !keys.includes(key))) {
    throw contractError(`${label} has missing or unexpected fields.`)
  }
}

function string(value: unknown, label: string, nullable = false): asserts value is string | null {
  if (nullable && value === null) return
  if (typeof value !== 'string' || value.trim() === '') throw contractError(`${label} must be a non-empty string${nullable ? ' or null' : ''}.`)
}

function integer(value: unknown, label: string, nullable = false): asserts value is number | null {
  if (nullable && value === null) return
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
    throw contractError(`${label} must be a non-negative safe integer${nullable ? ' or null' : ''}.`)
  }
}

function sha256(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) throw contractError(`${label} must be a lowercase SHA-256.`)
}

function requiredString(value: unknown, label: string): string {
  string(value, label)
  return value as string
}

function nullableString(value: unknown, label: string): string | null {
  string(value, label, true)
  return value as string | null
}

function safeInteger(value: unknown, label: string): number {
  integer(value, label)
  return value as number
}

function nullableInteger(value: unknown, label: string): number | null {
  integer(value, label, true)
  return value as number | null
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  if (typeof value !== 'string' || !allowed.includes(value as T)) throw contractError(`${label} is unknown.`)
  return value as T
}

function validNaiveTimestamp(value: unknown, label: string): string {
  const timestamp = requiredString(value, label)
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?$/.exec(timestamp)
  if (!match) throw contractError(`${label} must be a valid timezone-naive ISO timestamp.`)
  const [, year, month, day, hour, minute, second] = match.map((part) => part ?? '')
  const parsed = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second)))
  if (!Number.isFinite(parsed.getTime())
      || parsed.getUTCFullYear() !== Number(year)
      || parsed.getUTCMonth() + 1 !== Number(month)
      || parsed.getUTCDate() !== Number(day)
      || parsed.getUTCHours() !== Number(hour)
      || parsed.getUTCMinutes() !== Number(minute)
      || parsed.getUTCSeconds() !== Number(second)) {
    throw contractError(`${label} must be a valid timezone-naive ISO timestamp.`)
  }
  return timestamp
}

function validUtcTimestamp(value: unknown, label: string): string {
  const timestamp = requiredString(value, label)
  if (!Number.isFinite(Date.parse(timestamp)) || !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(timestamp)) {
    throw contractError(`${label} must include an explicit UTC offset.`)
  }
  return timestamp
}

function parseIssue(value: unknown): DataIssue {
  exactRecord(value, ['code', 'message', 'severity'], 'data-quality issue')
  return {
    code: requiredString(value.code, 'issue code'),
    message: requiredString(value.message, 'issue message'),
    severity: oneOf(value.severity, ['BLOCKER', 'RECONCILIATION_PENDING'], 'issue severity'),
  }
}

function parseDataQuality(value: unknown): DataQualityReport {
  exactRecord(value, [
    'status', 'schema_status', 'row_count', 'symbol', 'timeframe', 'period_enum',
    'timestamp_timezone', 'timezone_provenance', 'timestamp_min', 'timestamp_max',
    'identical_duplicate_rows', 'conflicting_duplicate_identities',
    'timestamp_discontinuity_status', 'broker_reconciliation_status', 'issues',
  ], 'data-quality report')
  if (!Array.isArray(value.issues)) throw contractError('data-quality issues must be an array.')
  const issues = value.issues.map(parseIssue)
  const status = oneOf(value.status, ['PASS', 'BLOCKED'], 'data-quality status')
  const schemaStatus = oneOf(value.schema_status, ['PASS', 'INVALID'], 'schema status')
  const rowCount = safeInteger(value.row_count, 'row count')
  const symbol = nullableString(value.symbol, 'symbol')
  const timeframe = nullableString(value.timeframe, 'timeframe')
  const periodEnum = nullableInteger(value.period_enum, 'period enum')
  const timestampTimezone = oneOf(value.timestamp_timezone, ['NAIVE_BROKER_SOURCE_TIME'], 'timestamp timezone')
  const timezoneProvenance = nullableString(value.timezone_provenance, 'timezone provenance')
  const timestampMin = value.timestamp_min === null ? null : validNaiveTimestamp(value.timestamp_min, 'minimum timestamp')
  const timestampMax = value.timestamp_max === null ? null : validNaiveTimestamp(value.timestamp_max, 'maximum timestamp')
  const identicalDuplicates = safeInteger(value.identical_duplicate_rows, 'identical duplicate count')
  const conflictingDuplicates = safeInteger(value.conflicting_duplicate_identities, 'conflicting duplicate count')
  const timestampDiscontinuityStatus = oneOf(value.timestamp_discontinuity_status, [
    'NOT_ASSESSED', 'NO_OBSERVED_DISCONTINUITY', 'OBSERVED_TIMESTAMP_DISCONTINUITY', 'GAP_DETECTION_UNAVAILABLE',
  ], 'timestamp discontinuity status')
  const brokerReconciliationStatus = oneOf(value.broker_reconciliation_status, [
    'NOT_ASSESSED', 'BROKER_RECONCILIATION_PENDING', 'BROKER_CONFIRMED_MISSING',
    'BROKER_CONFIRMED_NOT_MISSING', 'REPAIR_REQUIRED', 'REPAIR_VERIFIED',
  ], 'broker reconciliation status')
  const hasBlockingIssue = issues.some((issue) => issue.severity === 'BLOCKER')
  if ((status === 'BLOCKED') !== hasBlockingIssue) throw contractError('data-quality status contradicts its blocking issue list.')
  const periodNames: Record<number, string> = {
    1: 'M1', 2: 'M2', 3: 'M3', 4: 'M4', 5: 'M5', 6: 'M6', 10: 'M10', 12: 'M12', 15: 'M15', 20: 'M20', 30: 'M30',
    16385: 'H1', 16386: 'H2', 16387: 'H3', 16388: 'H4', 16390: 'H6', 16392: 'H8', 16396: 'H12',
    16408: 'D1', 32769: 'W1', 49153: 'MN1',
  }
  if ((periodEnum === null) !== (timeframe === null)
      || (periodEnum !== null && periodNames[periodEnum] !== timeframe)) {
    throw contractError('MQL timeframe enum and timeframe label disagree.')
  }
  if ((identicalDuplicates > 0) !== issues.some((issue) => issue.code === 'IDENTICAL_DUPLICATES_REQUIRE_EXPLICIT_RESOLUTION')
      || (conflictingDuplicates > 0) !== issues.some((issue) => issue.code === 'CONFLICTING_DUPLICATE_IDENTITY')) {
    throw contractError('duplicate counters contradict duplicate-identity evidence.')
  }
  if (status === 'PASS' && (schemaStatus !== 'PASS' || !rowCount || !symbol || !timeframe || periodEnum === null || !timezoneProvenance || !timestampMin || !timestampMax || identicalDuplicates || conflictingDuplicates || timestampDiscontinuityStatus === 'NOT_ASSESSED' || brokerReconciliationStatus !== 'BROKER_RECONCILIATION_PENDING')) {
    throw contractError('passing data-quality evidence contradicts mandatory summary fields.')
  }
  if (schemaStatus === 'INVALID' && status !== 'BLOCKED') throw contractError('invalid schema cannot have passing data-quality status.')
  if (schemaStatus === 'INVALID' && brokerReconciliationStatus !== 'NOT_ASSESSED') throw contractError('invalid schema cannot claim broker-reconciliation assessment.')
  if (timestampMin !== null && timestampMax !== null && Date.parse(`${timestampMin}Z`) > Date.parse(`${timestampMax}Z`)) {
    throw contractError('data-quality timestamp coverage is reversed.')
  }
  const observedGap = issues.some((issue) => issue.code === 'OBSERVED_TIMESTAMP_DISCONTINUITY' && issue.severity === 'RECONCILIATION_PENDING')
  const unavailableGapCheck = issues.some((issue) => issue.code === 'TIMEFRAME_GAP_INTERVAL_UNDEFINED' && issue.severity === 'RECONCILIATION_PENDING')
  if ((timestampDiscontinuityStatus === 'OBSERVED_TIMESTAMP_DISCONTINUITY') !== observedGap
      || (timestampDiscontinuityStatus === 'GAP_DETECTION_UNAVAILABLE') !== unavailableGapCheck
      || (timestampDiscontinuityStatus === 'NO_OBSERVED_DISCONTINUITY' && (observedGap || unavailableGapCheck))) {
    throw contractError('timestamp discontinuity status contradicts observed-gap evidence.')
  }
  if (schemaStatus === 'PASS' && brokerReconciliationStatus !== 'BROKER_RECONCILIATION_PENDING') {
    throw contractError('ONNX-02 has no broker-evidence authority to advance reconciliation status.')
  }
  if (timestampDiscontinuityStatus === 'GAP_DETECTION_UNAVAILABLE' && !unavailableGapCheck) {
    throw contractError('unavailable gap detection has no corresponding issue.')
  }
  return {
    status, schema_status: schemaStatus, row_count: rowCount, symbol, timeframe, period_enum: periodEnum,
    timestamp_timezone: timestampTimezone, timezone_provenance: timezoneProvenance,
    timestamp_min: timestampMin, timestamp_max: timestampMax,
    identical_duplicate_rows: identicalDuplicates, conflicting_duplicate_identities: conflictingDuplicates,
    timestamp_discontinuity_status: timestampDiscontinuityStatus,
    broker_reconciliation_status: brokerReconciliationStatus, issues,
  }
}

const SNAPSHOT_KEYS = [
  'snapshot_id', 'dataset_id', 'sha256', 'parent_snapshot_id', 'filename', 'source_path_sha256',
  'source_fingerprint', 'size_bytes', 'row_count', 'schema_id', 'schema_version', 'strategy_contract',
  'feature_contract', 'ea_source_sha256', 'ea_manifest_sha256', 'symbol', 'timeframe', 'period_enum',
  'timestamp_timezone', 'timezone_provenance', 'timestamp_min', 'timestamp_max',
  'timestamp_discontinuity_status', 'broker_reconciliation_status',
  'dq_status', 'dq', 'correction', 'evidence_class', 'created_utc',
] as const

export function parseOnnxDataSnapshot(value: unknown): OnnxDataSnapshot {
  exactRecord(value, SNAPSHOT_KEYS, 'snapshot')
  const snapshotId = requiredString(value.snapshot_id, 'snapshot id')
  const datasetId = requiredString(value.dataset_id, 'dataset id')
  if (!/^DS-[0-9a-f]{64}$/.test(datasetId)) throw contractError('dataset id format is invalid.')
  sha256(value.sha256, 'snapshot hash')
  if (snapshotId !== `SNP-${value.sha256}`) throw contractError('snapshot id does not bind its hash.')
  const parentSnapshotId = nullableString(value.parent_snapshot_id, 'parent snapshot id')
  if (parentSnapshotId !== null && !/^SNP-[0-9a-f]{64}$/.test(parentSnapshotId)) throw contractError('parent snapshot id format is invalid.')
  const filename = requiredString(value.filename, 'source filename')
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(filename) || filename === '.' || filename === '..') throw contractError('snapshot filename is invalid.')
  sha256(value.source_path_sha256, 'source path hash')
  exactRecord(value.source_fingerprint, ['file_id', 'size_bytes', 'modified_ns', 'created_ns'], 'source fingerprint')
  const fileId = requiredString(value.source_fingerprint.file_id, 'source file id')
  const fingerprintSize = safeInteger(value.source_fingerprint.size_bytes, 'source fingerprint size')
  const modifiedNs = requiredString(value.source_fingerprint.modified_ns, 'source modified timestamp')
  const createdNs = requiredString(value.source_fingerprint.created_ns, 'source created timestamp')
  const sizeBytes = safeInteger(value.size_bytes, 'snapshot size')
  const rowCount = safeInteger(value.row_count, 'snapshot row count')
  if (!fileId.includes(':') || !/^-?\d+$/.test(modifiedNs) || !/^-?\d+$/.test(createdNs) || sizeBytes <= 0
      || (parentSnapshotId === null && fingerprintSize !== sizeBytes)) {
    throw contractError('snapshot source fingerprint or size is inconsistent.')
  }
  const summary: Record<string, string | null> = {}
  for (const key of ['schema_id', 'schema_version', 'strategy_contract', 'feature_contract', 'timestamp_timezone', 'created_utc']) {
    summary[key] = requiredString(value[key], key)
  }
  sha256(value.ea_source_sha256, 'EA source hash')
  sha256(value.ea_manifest_sha256, 'EA manifest hash')
  if (summary.schema_id !== 'MAX_MTF_TRAINING_CSV_49_V1' || summary.schema_version !== '1.0'
      || summary.strategy_contract !== 'MAX_TRUE_MTF_DYNAMIC_V1' || summary.feature_contract !== 'CP32_TRUE_MTF_V1') {
    throw contractError('snapshot does not match the accepted MAX/CP32 source contract.')
  }
  const symbol = nullableString(value.symbol, 'snapshot symbol')
  const timeframe = nullableString(value.timeframe, 'snapshot timeframe')
  const periodEnum = nullableInteger(value.period_enum, 'snapshot timeframe enum')
  const timezoneProvenance = nullableString(value.timezone_provenance, 'snapshot timezone provenance')
  const timestampMin = value.timestamp_min === null ? null : validNaiveTimestamp(value.timestamp_min, 'snapshot minimum timestamp')
  const timestampMax = value.timestamp_max === null ? null : validNaiveTimestamp(value.timestamp_max, 'snapshot maximum timestamp')
  const timestampDiscontinuityStatus = oneOf(value.timestamp_discontinuity_status, ['NOT_ASSESSED', 'NO_OBSERVED_DISCONTINUITY', 'OBSERVED_TIMESTAMP_DISCONTINUITY', 'GAP_DETECTION_UNAVAILABLE'], 'snapshot timestamp discontinuity status')
  const brokerReconciliationStatus = oneOf(value.broker_reconciliation_status, ['NOT_ASSESSED', 'BROKER_RECONCILIATION_PENDING', 'BROKER_CONFIRMED_MISSING', 'BROKER_CONFIRMED_NOT_MISSING', 'REPAIR_REQUIRED', 'REPAIR_VERIFIED'], 'snapshot broker reconciliation status')
  const dqStatus = oneOf(value.dq_status, ['PASS', 'BLOCKED'], 'snapshot DQ status')
  const dq = parseDataQuality(value.dq)
  if (dq.status !== dqStatus || dq.row_count !== rowCount || dq.symbol !== symbol || dq.timeframe !== timeframe || dq.period_enum !== periodEnum
      || dq.timestamp_timezone !== summary.timestamp_timezone || dq.timezone_provenance !== timezoneProvenance
      || dq.timestamp_min !== timestampMin || dq.timestamp_max !== timestampMax
      || dq.timestamp_discontinuity_status !== timestampDiscontinuityStatus
      || dq.broker_reconciliation_status !== brokerReconciliationStatus) {
    throw contractError('snapshot summary contradicts its DQ report.')
  }
  let correction: Record<string, unknown> | null = null
  if (value.correction !== null) {
    exactRecord(value.correction, ['operation', 'confirmed_phrase', 'confirmation_scope', 'source_snapshot_id', 'before_sha256', 'backup_snapshot_sha256', 'after_sha256', 'removed_rows'], 'duplicate-correction lineage')
    correction = value.correction
    if (correction.operation !== 'REMOVE_IDENTICAL_DUPLICATE_ROWS_FROM_DERIVED_SNAPSHOT'
        || correction.confirmed_phrase !== 'REMOVE_IDENTICAL_DUPLICATES'
        || correction.source_snapshot_id !== parentSnapshotId
        || correction.before_sha256 !== correction.backup_snapshot_sha256
        || correction.before_sha256 !== parentSnapshotId?.slice(4)
        || correction.after_sha256 !== value.sha256
        || safeInteger(correction.removed_rows, 'removed duplicate row count') <= 0) {
      throw contractError('duplicate-correction lineage does not bind the parent and derived snapshot.')
    }
    requiredString(correction.confirmation_scope, 'duplicate-correction scope')
  } else if (parentSnapshotId !== null) {
    throw contractError('derived snapshot is missing its correction lineage.')
  }
  const evidenceClass = oneOf(value.evidence_class, ['OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI', 'SYNTHETIC_TEST_EVIDENCE'], 'snapshot evidence class')
  const createdUtc = validUtcTimestamp(summary.created_utc, 'snapshot creation time')
  if (symbol === null || timeframe === null || periodEnum === null || timezoneProvenance === null) {
    if (dqStatus === 'PASS') throw contractError('passing snapshot is missing its source identity or timezone provenance.')
  }
  return {
    snapshot_id: snapshotId, dataset_id: datasetId, sha256: value.sha256 as string, parent_snapshot_id: parentSnapshotId,
    filename, source_path_sha256: value.source_path_sha256 as string,
    source_fingerprint: { file_id: fileId, size_bytes: fingerprintSize, modified_ns: modifiedNs, created_ns: createdNs },
    size_bytes: sizeBytes, row_count: rowCount,
    schema_id: summary.schema_id as string, schema_version: summary.schema_version as string,
    strategy_contract: summary.strategy_contract as string, feature_contract: summary.feature_contract as string,
    ea_source_sha256: value.ea_source_sha256 as string, ea_manifest_sha256: value.ea_manifest_sha256 as string,
    symbol, timeframe, period_enum: periodEnum, timestamp_timezone: summary.timestamp_timezone as string,
    timezone_provenance: timezoneProvenance, timestamp_min: timestampMin, timestamp_max: timestampMax,
    timestamp_discontinuity_status: timestampDiscontinuityStatus,
    broker_reconciliation_status: brokerReconciliationStatus,
    dq_status: dqStatus, dq, correction, evidence_class: evidenceClass, created_utc: createdUtc,
  }
}

function parseEnvelope(value: unknown, expected: string[], statuses: readonly string[], label: string): Record<string, unknown> {
  exactRecord(value, expected, label)
  if (value.contract_version !== '2.0' || value.source !== 'BACKEND_ONNX_02_DATA_API') throw contractError(`${label} has unsupported contract identity.`)
  if (typeof value.status !== 'string' || !statuses.includes(value.status)) throw contractError(`${label} has unknown status.`)
  return value
}

const WINDOW_NAMES = ['DISCOVERY', 'TOURNAMENT', 'FORWARD'] as const

function parseWindowConfig(value: unknown, snapshot: Pick<OnnxDataSnapshot, 'snapshot_id' | 'sha256' | 'timezone_provenance' | 'timestamp_min' | 'timestamp_max'>): Record<string, unknown> {
  exactRecord(value, [
    'window_config_id', 'snapshot_id', 'snapshot_sha256', 'revision', 'timezone_provenance', 'windows', 'validation', 'created_utc',
  ], 'window configuration')
  const id = requiredString(value.window_config_id, 'window configuration id')
  if (!/^WIN-[0-9a-f]{64}$/.test(id) || value.snapshot_id !== snapshot.snapshot_id || value.snapshot_sha256 !== snapshot.sha256) {
    throw contractError('window configuration identity does not bind the current snapshot.')
  }
  const revision = safeInteger(value.revision, 'window configuration revision')
  if (revision < 1 || value.timezone_provenance !== snapshot.timezone_provenance) throw contractError('window configuration revision or timezone provenance is invalid.')
  exactRecord(value.windows, WINDOW_NAMES, 'research windows')
  const ranges: Record<string, { from: string; to: string }> = {}
  for (const name of WINDOW_NAMES) {
    exactRecord(value.windows[name], ['from', 'to'], `${name} window`)
    const from = validNaiveTimestamp(value.windows[name].from, `${name} start`)
    const to = validNaiveTimestamp(value.windows[name].to, `${name} end`)
    if (Date.parse(`${from}Z`) > Date.parse(`${to}Z`)) throw contractError(`${name} window is reversed.`)
    ranges[name] = { from, to }
  }
  const discovery = ranges.DISCOVERY
  const tournament = ranges.TOURNAMENT
  const forward = ranges.FORWARD
  if (!(Date.parse(`${discovery.to}Z`) < Date.parse(`${tournament.from}Z`)
      && Date.parse(`${tournament.to}Z`) < Date.parse(`${forward.from}Z`))) {
    throw contractError('research windows are overlapping or out of order.')
  }
  exactRecord(value.validation, ['status', 'timezone_provenance', 'windows', 'issues'], 'window validation')
  if (value.validation.status !== 'PASS' || value.validation.timezone_provenance !== snapshot.timezone_provenance
      || !Array.isArray(value.validation.windows) || value.validation.windows.length !== 3 || !Array.isArray(value.validation.issues)
      || value.validation.issues.length !== 0) {
    throw contractError('persisted window validation is not a complete passing validation.')
  }
  const validatedWindows = value.validation.windows.map((item: unknown, index: number) => {
    exactRecord(item, ['name', 'from', 'to', 'row_count'], 'validated research window')
    const expectedName = WINDOW_NAMES[index]
    const range = ranges[expectedName]
    const name = requiredString(item.name, 'validated window name')
    const from = validNaiveTimestamp(item.from, 'validated window start')
    const to = validNaiveTimestamp(item.to, 'validated window end')
    const rowCount = safeInteger(item.row_count, 'validated window row count')
    if (name !== expectedName || Date.parse(`${from}Z`) !== Date.parse(`${range.from}Z`)
        || Date.parse(`${to}Z`) !== Date.parse(`${range.to}Z`) || rowCount <= 0) {
      throw contractError('validated window evidence contradicts its frozen range.')
    }
    return { name, from, to, row_count: rowCount }
  })
  if (new Date(`${ranges.DISCOVERY.from}Z`) < new Date(`${snapshot.timestamp_min ?? ''}Z`)
      || new Date(`${ranges.FORWARD.to}Z`) > new Date(`${snapshot.timestamp_max ?? ''}Z`)) {
    throw contractError('window configuration lies outside immutable snapshot coverage.')
  }
  return {
    window_config_id: id, snapshot_id: snapshot.snapshot_id, snapshot_sha256: snapshot.sha256, revision,
    timezone_provenance: snapshot.timezone_provenance, windows: ranges,
    validation: {
      status: 'PASS', timezone_provenance: snapshot.timezone_provenance,
      windows: validatedWindows, issues: [],
    },
    created_utc: validUtcTimestamp(value.created_utc, 'window configuration creation time'),
  }
}

function parseReadinessEvidence(
  value: unknown,
  snapshot: Pick<OnnxDataSnapshot, 'snapshot_id' | 'sha256' | 'evidence_class' | 'dq_status' | 'timestamp_discontinuity_status' | 'broker_reconciliation_status'>,
  windowConfig: Record<string, unknown>,
): Record<string, unknown> {
  if (snapshot.dq_status !== 'PASS'
      || !['BROKER_CONFIRMED_NOT_MISSING', 'REPAIR_VERIFIED'].includes(snapshot.broker_reconciliation_status)
      || snapshot.evidence_class === 'SYNTHETIC_TEST_EVIDENCE') {
    throw contractError('DATA_READY evidence is prohibited for blocked, unreconciled, or synthetic data.')
  }
  exactRecord(value, ['readiness_id', 'snapshot_id', 'snapshot_sha256', 'window_config_id', 'evidence', 'created_utc'], 'DATA_READY evidence')
  const readinessId = requiredString(value.readiness_id, 'readiness id')
  if (!/^READY-[0-9a-f]{64}$/.test(readinessId)
      || value.snapshot_id !== snapshot.snapshot_id || value.snapshot_sha256 !== snapshot.sha256
      || value.window_config_id !== windowConfig.window_config_id) {
    throw contractError('DATA_READY evidence does not bind the current snapshot and windows.')
  }
  exactRecord(value.evidence, [
    'status', 'snapshot_id', 'snapshot_sha256', 'window_config_id', 'dq_status',
    'timestamp_discontinuity_status', 'broker_reconciliation_status', 'evidence_class', 'owner_runtime_execution',
  ], 'DATA_READY evidence payload')
  if (value.evidence.status !== 'DATA_READY' || value.evidence.snapshot_id !== snapshot.snapshot_id
      || value.evidence.snapshot_sha256 !== snapshot.sha256 || value.evidence.window_config_id !== windowConfig.window_config_id
      || value.evidence.dq_status !== 'PASS'
      || value.evidence.timestamp_discontinuity_status !== snapshot.timestamp_discontinuity_status
      || value.evidence.broker_reconciliation_status !== snapshot.broker_reconciliation_status
      || value.evidence.evidence_class !== snapshot.evidence_class
      || value.evidence.owner_runtime_execution !== 'NOT_PERFORMED_BY_SOURCE_CI') {
    throw contractError('DATA_READY evidence payload contradicts the verified source boundary.')
  }
  return {
    readiness_id: readinessId, snapshot_id: snapshot.snapshot_id, snapshot_sha256: snapshot.sha256,
    window_config_id: windowConfig.window_config_id, evidence: value.evidence,
    created_utc: validUtcTimestamp(value.created_utc, 'DATA_READY evidence creation time'),
  }
}

function parseWindowValidation(value: unknown): {
  status: 'PASS' | 'BLOCKED'
  timezone_provenance: string | null
  windows: Array<{ name: string; from: string; to: string; row_count: number }>
  issues: DataIssue[]
} {
  exactRecord(value, ['status', 'timezone_provenance', 'windows', 'issues'], 'window validation result')
  const status = oneOf(value.status, ['PASS', 'BLOCKED'], 'window validation status')
  const timezoneProvenance = nullableString(value.timezone_provenance, 'window timezone provenance')
  if (!Array.isArray(value.windows) || !Array.isArray(value.issues)) throw contractError('window validation arrays are malformed.')
  const issues = value.issues.map(parseIssue)
  if ((status === 'PASS' && (value.windows.length !== 3 || issues.length !== 0))
      || (status === 'BLOCKED' && issues.length === 0)
      || (value.windows.length !== 0 && value.windows.length !== 3)) {
    throw contractError('window validation status contradicts its evidence.')
  }
  const windows = value.windows.map((item: unknown, index: number) => {
    exactRecord(item, ['name', 'from', 'to', 'row_count'], 'window validation item')
    const name = requiredString(item.name, 'window name')
    const from = validNaiveTimestamp(item.from, 'window start')
    const to = validNaiveTimestamp(item.to, 'window end')
    const rowCount = safeInteger(item.row_count, 'window row count')
    if (name !== WINDOW_NAMES[index] || Date.parse(`${from}Z`) > Date.parse(`${to}Z`)
        || (status === 'PASS' && rowCount === 0)) throw contractError('window validation item is inconsistent.')
    return { name, from, to, row_count: rowCount }
  })
  return { status, timezone_provenance: timezoneProvenance, windows, issues }
}

function parseSnapshotOperation(value: unknown): {
  status: OnnxDataStatus
  snapshot: OnnxDataSnapshot
  data_quality: DataQualityReport
  first_blocker: string | null
} {
  const result = parseEnvelope(value,
    ['contract_version', 'source', 'status', 'snapshot', 'data_quality', 'first_blocker', 'readiness'],
    ['SNAPSHOT_AUDITED', 'DQ_BLOCKED', 'SYNTHETIC_TEST_EVIDENCE'], 'snapshot result')
  const snapshot = parseOnnxDataSnapshot(result.snapshot)
  const dataQuality = parseDataQuality(result.data_quality)
  const status = oneOf(result.status, ['SNAPSHOT_AUDITED', 'DQ_BLOCKED', 'SYNTHETIC_TEST_EVIDENCE'], 'snapshot result status')
  const blocker = nullableString(result.first_blocker, 'first blocker')
  const expectedStatus = snapshot.evidence_class === 'SYNTHETIC_TEST_EVIDENCE'
    ? 'SYNTHETIC_TEST_EVIDENCE' : snapshot.dq_status === 'PASS' ? 'SNAPSHOT_AUDITED' : 'DQ_BLOCKED'
  if (status !== expectedStatus || dataQuality.status !== snapshot.dq_status
      || dataQuality.row_count !== snapshot.row_count
      || dataQuality.issues.length !== snapshot.dq.issues.length
      || dataQuality.issues.some((issue, index) => issue.code !== snapshot.dq.issues[index]?.code)
      || blocker !== (dataQuality.issues.find((issue) => issue.severity === 'BLOCKER')?.code ?? null) || result.readiness !== null) {
    throw contractError('snapshot result status or evidence contradicts the immutable snapshot.')
  }
  return { status, snapshot, data_quality: dataQuality, first_blocker: blocker }
}

export function parseOnnxDataWorkspace(value: unknown): OnnxDataWorkspace {
  const result = parseEnvelope(
    value,
    ['contract_version', 'source', 'status', 'implementation_status', 'real_data_readiness', 'latest_snapshot', 'window_config', 'readiness_evidence', 'snapshots', 'scientific_execution', 'first_blocker'],
    ONNX_DATA_STATUSES,
    'workspace',
  )
  if (result.implementation_status !== 'IMPLEMENTED' || result.scientific_execution !== 'NOT_IMPLEMENTED') throw contractError('workspace capability boundary is invalid.')
  const status = oneOf(result.status, ONNX_DATA_STATUSES, 'workspace status')
  const realReadiness = oneOf(result.real_data_readiness, ['NOT_PROVEN', 'DATA_READY'], 'real-data readiness status')
  const firstBlocker = nullableString(result.first_blocker, 'first blocker')
  const latest = result.latest_snapshot === null ? null : parseOnnxDataSnapshot(result.latest_snapshot)
  if (!Array.isArray(result.snapshots)) throw contractError('snapshot history must be an array.')
  if (result.snapshots.length > 10) throw contractError('snapshot history exceeds the backend response limit.')
  const snapshots = result.snapshots.map(parseOnnxDataSnapshot)
  if (new Set(snapshots.map((item) => item.snapshot_id)).size !== snapshots.length) throw contractError('snapshot history contains duplicate identities.')
  for (let index = 1; index < snapshots.length; index += 1) {
    if (Date.parse(snapshots[index - 1].created_utc) < Date.parse(snapshots[index].created_utc)) throw contractError('snapshot history is not newest-first.')
  }
  if ((latest === null) !== (snapshots.length === 0)
      || (latest !== null && (snapshots[0].snapshot_id !== latest.snapshot_id || snapshots[0].sha256 !== latest.sha256))) {
    throw contractError('latest snapshot contradicts snapshot history.')
  }
  const expectedStatus = latest === null ? 'NOT_STARTED'
    : latest.evidence_class === 'SYNTHETIC_TEST_EVIDENCE' ? 'SYNTHETIC_TEST_EVIDENCE'
      : latest.dq_status === 'BLOCKED' ? 'DQ_BLOCKED'
        : result.readiness_evidence !== null ? 'DATA_READY'
          : result.window_config !== null ? 'WINDOWS_VALIDATED' : 'SNAPSHOT_AUDITED'
  if (status !== expectedStatus) throw contractError('workspace status contradicts persisted snapshot evidence.')
  const windowConfig = result.window_config === null
    ? null
    : latest === null ? (() => { throw contractError('window configuration exists without a snapshot.') })()
      : parseWindowConfig(result.window_config, latest)
  const readinessEvidence = result.readiness_evidence === null
    ? null
    : latest === null || windowConfig === null ? (() => { throw contractError('DATA_READY evidence exists without snapshot windows.') })()
      : parseReadinessEvidence(result.readiness_evidence, latest, windowConfig)
  if ((realReadiness === 'DATA_READY') !== (readinessEvidence !== null)
      || (latest?.evidence_class === 'SYNTHETIC_TEST_EVIDENCE' && readinessEvidence !== null)) {
    throw contractError('real-data readiness and evidence presence disagree.')
  }
  const expectedBlocker = latest === null ? 'NO_IMMUTABLE_SNAPSHOT'
    : latest.dq_status === 'BLOCKED' ? latest.dq.issues.find((issue) => issue.severity === 'BLOCKER')?.code ?? 'DATA_QUALITY_BLOCKED'
      : windowConfig === null ? 'THREE_WINDOWS_NOT_VALIDATED'
        : readinessEvidence === null
          ? latest.evidence_class === 'SYNTHETIC_TEST_EVIDENCE' ? 'SYNTHETIC_TEST_EVIDENCE_NOT_REAL_DATA' : latest.broker_reconciliation_status
          : null
  if (firstBlocker !== expectedBlocker) throw contractError('workspace blocker contradicts persisted evidence.')
  return {
    contract_version: '2.0', source: 'BACKEND_ONNX_02_DATA_API', status,
    implementation_status: 'IMPLEMENTED', real_data_readiness: realReadiness,
    latest_snapshot: latest, window_config: windowConfig, readiness_evidence: readinessEvidence,
    snapshots, scientific_execution: 'NOT_IMPLEMENTED', first_blocker: firstBlocker,
  }
}

export function parseOnnxDataPreflight(value: unknown): OnnxDataPreflight {
  const result = parseEnvelope(
    value,
    ['contract_version', 'source', 'status', 'snapshot_permitted', 'source_identity', 'authority', 'data_quality', 'first_blocker', 'evidence_class'],
    ['PREFLIGHT_PASS', 'PREFLIGHT_BLOCKED'],
    'preflight',
  )
  if (typeof result.snapshot_permitted !== 'boolean') throw contractError('snapshot permission must be boolean.')
  exactRecord(result.source_identity, ['filename', 'path_sha256', 'sha256', 'fingerprint', 'size_bytes'], 'source identity')
  const filename = requiredString(result.source_identity.filename, 'source filename')
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(filename) || filename === '.' || filename === '..') throw contractError('source filename is invalid.')
  sha256(result.source_identity.path_sha256, 'source path hash')
  sha256(result.source_identity.sha256, 'source content hash')
  const sourceSize = safeInteger(result.source_identity.size_bytes, 'source size')
  exactRecord(result.source_identity.fingerprint, ['file_id', 'size_bytes', 'modified_ns', 'created_ns'], 'source fingerprint')
  const fingerprint = {
    file_id: requiredString(result.source_identity.fingerprint.file_id, 'source fingerprint file id'),
    size_bytes: safeInteger(result.source_identity.fingerprint.size_bytes, 'source fingerprint size'),
    modified_ns: requiredString(result.source_identity.fingerprint.modified_ns, 'source modified timestamp'),
    created_ns: requiredString(result.source_identity.fingerprint.created_ns, 'source created timestamp'),
  }
  if (!fingerprint.file_id.includes(':') || fingerprint.size_bytes !== sourceSize || !/^-?\d+$/.test(fingerprint.modified_ns) || !/^-?\d+$/.test(fingerprint.created_ns)) {
    throw contractError('source identity and fingerprint disagree.')
  }
  exactRecord(result.authority, ['strategy_contract', 'feature_contract', 'schema_id', 'schema_version', 'ea_source_sha256', 'ea_manifest_sha256'], 'source authority')
  const authority = {
    strategy_contract: requiredString(result.authority.strategy_contract, 'strategy contract'),
    feature_contract: requiredString(result.authority.feature_contract, 'feature contract'),
    schema_id: requiredString(result.authority.schema_id, 'schema id'),
    schema_version: requiredString(result.authority.schema_version, 'schema version'),
    ea_source_sha256: requiredString(result.authority.ea_source_sha256, 'EA source hash'),
    ea_manifest_sha256: requiredString(result.authority.ea_manifest_sha256, 'EA manifest hash'),
  }
  if (authority.strategy_contract !== 'MAX_TRUE_MTF_DYNAMIC_V1' || authority.feature_contract !== 'CP32_TRUE_MTF_V1'
      || authority.schema_id !== 'MAX_MTF_TRAINING_CSV_49_V1' || authority.schema_version !== '1.0') {
    throw contractError('source authority does not match the accepted MAX data contract.')
  }
  sha256(authority.ea_source_sha256, 'EA source hash')
  sha256(authority.ea_manifest_sha256, 'EA manifest hash')
  const dataQuality = parseDataQuality(result.data_quality)
  const status = oneOf(result.status, ['PREFLIGHT_PASS', 'PREFLIGHT_BLOCKED'], 'preflight status')
  const firstBlocker = nullableString(result.first_blocker, 'first blocker')
  const evidenceClass = oneOf(result.evidence_class, ['OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI', 'SYNTHETIC_TEST_EVIDENCE'], 'preflight evidence class')
  if ((status === 'PREFLIGHT_PASS') !== (dataQuality.status === 'PASS')
      || (status === 'PREFLIGHT_PASS' && (!result.snapshot_permitted || firstBlocker !== null))
      || (status === 'PREFLIGHT_BLOCKED' && firstBlocker !== (dataQuality.issues.find((issue) => issue.severity === 'BLOCKER')?.code ?? null))) {
    throw contractError('preflight status, DQ result, snapshot permission, and blocker contradict each other.')
  }
  return {
    contract_version: '2.0', source: 'BACKEND_ONNX_02_DATA_API', status,
    snapshot_permitted: result.snapshot_permitted,
    source_identity: {
      filename, path_sha256: result.source_identity.path_sha256 as string, sha256: result.source_identity.sha256 as string,
      fingerprint, size_bytes: sourceSize,
    },
    authority, data_quality: dataQuality, first_blocker: firstBlocker, evidence_class: evidenceClass,
  }
}

function parseResponse<T>(response: Response, body: unknown, parser: (value: unknown) => T): T {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    if (isRecord(body)) {
      const rawDetail = body.detail
      if (typeof rawDetail === 'string') detail = rawDetail
      else if (isRecord(rawDetail) && typeof rawDetail.message === 'string') detail = rawDetail.message
    }
    throw new Error(detail)
  }
  return parser(body)
}

async function request<T>(path: string, parser: (value: unknown) => T, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw contractError('response body is not valid JSON.')
  }
  return parseResponse(response, payload, parser)
}

export function fetchOnnxDataWorkspace(signal?: AbortSignal) {
  return request('/api/v2/onnx/data/workspace', parseOnnxDataWorkspace, { method: 'GET', signal })
}

export function preflightOnnxData(payload: { source_path?: string; timezone_provenance?: string } = {}) {
  return request('/api/v2/onnx/data/preflight', parseOnnxDataPreflight, { method: 'POST', body: JSON.stringify(payload) })
}

export function createOnnxSnapshot(payload: { source_path?: string; timezone_provenance?: string; expected_source_sha256: string; confirmed: true }) {
  return request('/api/v2/onnx/data/snapshots', parseSnapshotOperation, { method: 'POST', body: JSON.stringify(payload) })
}

export function resolveOnnxIdenticalDuplicates(snapshot: OnnxDataSnapshot) {
  return request(`/api/v2/onnx/data/snapshots/${encodeURIComponent(snapshot.snapshot_id)}/resolve-identical-duplicates`, (value) => {
    const parsed = parseSnapshotOperation(value)
    if (parsed.snapshot.parent_snapshot_id !== snapshot.snapshot_id || parsed.snapshot.correction === null
        || parsed.snapshot.correction.before_sha256 !== snapshot.sha256) throw contractError('duplicate-correction lineage is missing.')
    return { status: parsed.status, snapshot: parsed.snapshot, first_blocker: parsed.first_blocker }
  }, {
    method: 'POST',
    body: JSON.stringify({ expected_snapshot_sha256: snapshot.sha256, confirmation: 'REMOVE_IDENTICAL_DUPLICATES' }),
  })
}

export function validateOnnxWindows(payload: {
  snapshot_id: string
  snapshot_sha256: string
  timezone_provenance: string
  windows: Record<'DISCOVERY' | 'TOURNAMENT' | 'FORWARD', { from: string; to: string }>
}) {
  return request('/api/v2/onnx/data/windows/validate', (value) => {
    const result = parseEnvelope(value,
      ['contract_version', 'source', 'status', 'window_config', 'validation', 'readiness', 'first_blocker'],
      ['WINDOWS_BLOCKED', 'WINDOWS_VALIDATED', 'DATA_READY', 'SYNTHETIC_TEST_EVIDENCE'], 'window validation result')
    const status = oneOf(result.status, ['WINDOWS_BLOCKED', 'WINDOWS_VALIDATED', 'DATA_READY', 'SYNTHETIC_TEST_EVIDENCE'], 'window result status')
    const validation = parseWindowValidation(result.validation)
    const blocker = nullableString(result.first_blocker, 'first blocker')
    if (validation.timezone_provenance !== payload.timezone_provenance) throw contractError('window validation timezone provenance differs from the submitted authority.')
    if (result.window_config === null) {
      if (status !== 'WINDOWS_BLOCKED' || validation.status !== 'BLOCKED' || result.readiness !== null
          || blocker !== validation.issues[0]?.code) throw contractError('blocked window result contains inconsistent state.')
      return { status, validation, window_config: null, readiness: null, first_blocker: blocker }
    }
    const config = parseWindowConfig(result.window_config, {
      snapshot_id: payload.snapshot_id,
      sha256: payload.snapshot_sha256,
      timezone_provenance: payload.timezone_provenance,
      timestamp_min: null,
      timestamp_max: null,
    })
    if (validation.status !== 'PASS' || config.snapshot_id !== payload.snapshot_id
        || config.snapshot_sha256 !== payload.snapshot_sha256
        || JSON.stringify(config.windows) !== JSON.stringify(payload.windows)) {
      throw contractError('validated window result contradicts the submitted frozen input.')
    }
    let readiness: Record<string, unknown> | null = null
    if (result.readiness !== null) {
      readiness = parseReadinessEvidence(result.readiness, {
        snapshot_id: payload.snapshot_id,
        sha256: payload.snapshot_sha256,
        evidence_class: 'OWNER_SELECTED_SOURCE_NOT_EXECUTED_IN_SOURCE_CI',
        dq_status: 'PASS',
        timestamp_discontinuity_status: 'NO_OBSERVED_DISCONTINUITY',
        broker_reconciliation_status: 'BROKER_CONFIRMED_NOT_MISSING',
      }, config)
    }
    if (status === 'DATA_READY' && (readiness === null || blocker !== null)) throw contractError('DATA_READY requires verified readiness evidence and no blocker.')
    if (status === 'SYNTHETIC_TEST_EVIDENCE' && (readiness !== null || blocker !== null)) throw contractError('synthetic evidence cannot imply real readiness or a readiness blocker.')
    if (status === 'WINDOWS_VALIDATED' && (readiness !== null || blocker !== 'BROKER_RECONCILIATION_PENDING')) throw contractError('validated windows have an inconsistent broker-reconciliation blocker.')
    if (!['DATA_READY', 'SYNTHETIC_TEST_EVIDENCE', 'WINDOWS_VALIDATED'].includes(status)) throw contractError('window result status contradicts passing validation.')
    return { status, validation, window_config: config, readiness, first_blocker: blocker }
  }, { method: 'POST', body: JSON.stringify(payload) })
}
