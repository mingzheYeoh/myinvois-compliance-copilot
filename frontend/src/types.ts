export interface HealthResponse {
  status: 'ok' | 'degraded';
  guideline_versions: Record<string, string>;
  db: 'ok' | 'fail';
  budget: {
    limit: number;
    used: number | null;
    remaining: number | null;
  };
}

export interface Citation {
  doc: string;
  version: string;
  section: string;
  page: number;
}

export interface ChatResponse {
  answer: string;
  citations: Citation[];
  route: string | null;
  thread_id: string;
  models?: Record<string, number>;
  tokens?: number;
}

export interface ChatError {
  error: string;
  resets_at?: string;
  budget?: number;
  limit?: number;
  type?: string;
}

export interface FieldIssue {
  no: number;
  name: string;
  category: string | null;
  status: string;
  condition: string | null;
  section: string;
}

export interface ValidateResponse {
  valid: boolean;
  checked: number;
  present: string[];
  missing_mandatory: FieldIssue[];
  check_conditional: FieldIssue[];
  unknown_keys: string[];
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  route?: string | null;
  citations?: Citation[];
  tokens?: number;
  models?: Record<string, number>;
  isError?: boolean;
  errorDetail?: ChatError;
  timestamp: string;
}
