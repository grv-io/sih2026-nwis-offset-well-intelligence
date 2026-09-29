export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

/** Thrown by the static demo build when a request falls outside what was recorded
 *  (a well combination, depth or question that has no snapshot). UI code can show
 *  a friendly, translated note for it instead of a generic error. */
export class StaticMissError extends ApiError {
  constructor(message: string) {
    super(404, message)
  }
}
