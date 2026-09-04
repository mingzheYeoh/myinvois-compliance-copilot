import React, { useState, useRef, useEffect } from 'react';
import { ChatMessage, Citation, ChatResponse } from '../types';

interface AskChatProps {
  isHealthy: boolean;
  onSwitchToCheckInvoice: () => void;
}

const MAX_CHARS = 2000;

export const AskChat: React.FC<AskChatProps> = ({
  isHealthy,
  onSwitchToCheckInvoice,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [quotaExhaustedError, setQuotaExhaustedError] = useState<{
    error: string;
    resets_at?: string;
  } | null>(null);
  const [rateLimitError, setRateLimitError] = useState<string | null>(null);

  const threadIdRef = useRef<string | null>(null);
  const lastSubmittedRef = useRef<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading, networkError, quotaExhaustedError, rateLimitError]);

  const handleResetSession = () => {
    setMessages([]);
    threadIdRef.current = null;
    setInput('');
    setNetworkError(null);
    setQuotaExhaustedError(null);
    setRateLimitError(null);
    textareaRef.current?.focus();
  };

  const submitQuestion = async (questionText: string) => {
    const trimmed = questionText.trim();
    if (!trimmed || loading || !isHealthy) return;

    if (trimmed.length > MAX_CHARS) {
      return;
    }

    lastSubmittedRef.current = trimmed;
    setNetworkError(null);
    setQuotaExhaustedError(null);
    setRateLimitError(null);

    const userMessage: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const response = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: trimmed,
          thread_id: threadIdRef.current || undefined,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        if (response.status === 429) {
          if (data.resets_at) {
            setQuotaExhaustedError({
              error: data.error || 'The daily question quota is used up.',
              resets_at: data.resets_at,
            });
          } else {
            setRateLimitError('Slow down, try again in a minute.');
          }
        } else if (response.status === 413) {
          setNetworkError(data.error || `Message exceeds limit of ${MAX_CHARS} characters.`);
        } else {
          setNetworkError(data.error || 'Something went wrong handling that request.');
        }

        setInput(trimmed);
        setLoading(false);
        return;
      }

      const chatData = data as ChatResponse;
      if (chatData.thread_id) {
        threadIdRef.current = chatData.thread_id;
      }

      const assistantMessage: ChatMessage = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: chatData.answer,
        messageId: chatData.message_id,
        route: chatData.route,
        citations: chatData.citations || [],
        tokens: chatData.tokens,
        models: chatData.models,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      setNetworkError('Could not reach the server. Please check your connection.');
      setInput(trimmed);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submitQuestion(input);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submitQuestion(input);
    }
  };

  const handleRetry = () => {
    if (lastSubmittedRef.current) {
      submitQuestion(lastSubmittedRef.current);
    }
  };

  const charCount = input.length;
  const isOverLimit = charCount > MAX_CHARS;

  const formatResetTime = (isoString?: string) => {
    if (!isoString) return 'midnight UTC';
    try {
      const d = new Date(isoString);
      return `${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (${d.toLocaleDateString()})`;
    } catch {
      return isoString;
    }
  };

  const getRouteLabel = (route?: string | null) => {
    if (!route) return 'General';
    const r = route.toLowerCase();
    if (r.includes('applicab')) return 'Applicability';
    if (r.includes('field')) return 'Field Check';
    return 'General';
  };

  // One click, no form. The server already holds the question, answer, citations,
  // route, model and LangSmith run id for this answer, so nothing needs collecting
  // from the user -- which is also why there is no free-text box to moderate.
  const reportProblem = async (rowId: string, messageId: string) => {
    setMessages((prev) =>
      prev.map((x) => (x.id === rowId ? { ...x, reported: 'sending' as const } : x)),
    );
    let state: 'logged' | 'failed' = 'failed';
    try {
      const res = await fetch('/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          thread_id: threadIdRef.current,
          message_id: messageId,
        }),
      });
      if (res.ok) state = 'logged';
    } catch {
      state = 'failed';
    }
    setMessages((prev) =>
      prev.map((x) => (x.id === rowId ? { ...x, reported: state } : x)),
    );
  };

  // Helper: deduplicate bottom citations list
  const getDedupedCitations = (citations: Citation[]): Citation[] => {
    const seen = new Set<string>();
    const result: Citation[] = [];
    for (const c of citations) {
      const key = `${c.doc}-${c.version}-${c.section}-${c.page}`.toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        result.push(c);
      }
    }
    return result;
  };

  // Helper: parse inline markdown bold and inline citations with deduplication
  const renderInlineTokens = (text: string, seenCitations: Set<string>): React.ReactNode[] => {
    // Regex splits on either **bold** or [Citation ...]
    const TOKEN_REGEX = /(\*\*[^*]+\*\*|\[(?:(?:General\s+)?Guideline|Specific\s+Guideline|FAQ)[^\]]+\])/g;
    const parts = text.split(TOKEN_REGEX);

    return parts.map((part, idx) => {
      if (!part) return null;

      // Markdown bold: **word**
      if (part.startsWith('**') && part.endsWith('**') && part.length >= 4) {
        const content = part.slice(2, -2);
        return (
          <strong key={idx} className="answer-bold">
            {content}
          </strong>
        );
      }

      // Inline citation: [Guideline ...]
      if (part.startsWith('[') && part.endsWith(']')) {
        const norm = part.toLowerCase().replace(/\s+/g, ' ');
        if (seenCitations.has(norm)) {
          // Drop repetition: if the same ref is cited twice in one answer, keep the first
          return null;
        }
        seenCitations.add(norm);
        return (
          <span key={idx} className="inline-citation">
            {part}
          </span>
        );
      }

      return <span key={idx}>{part}</span>;
    });
  };

  // Render full formatted answer
  const renderFormattedAnswer = (text: string) => {
    const lines = text.split(/\n\n+/);
    const seenCitations = new Set<string>();

    return lines.map((paragraph, pIdx) => {
      const trimmed = paragraph.trim();
      if (!trimmed) return null;

      // Check if it's the LHDN notice line
      if (/confirm.*with LHDN/i.test(trimmed)) {
        return (
          <div key={`notice-${pIdx}`} className="lhdn-notice-box">
            {trimmed.replace(/^["'\s]+|["'\s]+$/g, '')}
          </div>
        );
      }

      // Check if it's the assumption sentence ("I assumed today's date...", "no transaction date was given, so today was assumed")
      const isAssumption = /^(?:note|assumption):?\s*(?:no transaction date was given|i assumed today'?s?\s+date|today was assumed)/i.test(trimmed)
        || /(?:no transaction date was given|assumed today'?s?\s+date)/i.test(trimmed);

      if (isAssumption) {
        // Assumption sentence must not carry a citation — strip any bracketed citation
        const cleanAssumption = trimmed.replace(/\[(?:(?:General\s+)?Guideline|Specific\s+Guideline|FAQ)[^\]]+\]/g, '').trim();
        return (
          <div key={`assumption-${pIdx}`} className="assumption-note">
            {cleanAssumption}
          </div>
        );
      }

      // Standard body paragraph
      return (
        <p key={`p-${pIdx}`} className="answer-paragraph">
          {renderInlineTokens(trimmed, seenCitations)}
        </p>
      );
    });
  };

  return (
    <div className="view-container">
      <div className="chat-thread">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <h3>Ask anything about e-Invoice rules</h3>
            <p>
              Answers cite the official IRBM Guideline and FAQ versions and sections.
            </p>
            <div className="sample-prompts">
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('What is the exemption threshold for e-Invoice implementation?')}
              >
                &ldquo;What is the exemption threshold for e-Invoice implementation?&rdquo; &rarr;
              </button>
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('My business started in 2024 with RM2M turnover. When must I implement e-Invoice?')}
              >
                &ldquo;My business started in 2024 with RM2M turnover. When must I implement e-Invoice?&rdquo; &rarr;
              </button>
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('Can I issue a consolidated e-Invoice for a RM12,000 sale?')}
              >
                &ldquo;Can I issue a consolidated e-Invoice for a RM12,000 sale?&rdquo; &rarr;
              </button>
            </div>
          </div>
        ) : (
          messages.map((m) => {
            if (m.role === 'user') {
              return (
                <div key={m.id} className="msg-row user">
                  <div className="msg-bubble-user">{m.content}</div>
                </div>
              );
            }

            const dedupedCites = getDedupedCitations(m.citations || []);

            return (
              <div key={m.id} className="msg-row assistant">
                <div className="assistant-area">
                  <div className="msg-header-bar">
                    <span className="route-badge">
                      {getRouteLabel(m.route)}
                    </span>
                  </div>

                  <div className="answer-body">
                    {renderFormattedAnswer(m.content)}
                  </div>

                  {dedupedCites.length > 0 && (
                    <div className="citations-block">
                      <ul className="citations-list">
                        {dedupedCites.map((c: Citation, cIdx: number) => (
                          <li key={cIdx} className="citation-item">
                            {c.doc} v{c.version} &sect;{c.section}, p{c.page}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {m.messageId && (
                    <div className="report-row">
                      {m.reported === 'logged' ? (
                        <span className="report-done">Thanks - logged.</span>
                      ) : m.reported === 'failed' ? (
                        <span className="report-done">Could not log that.</span>
                      ) : (
                        <button
                          type="button"
                          className="report-link"
                          disabled={m.reported === 'sending'}
                          onClick={() => reportProblem(m.id, m.messageId!)}
                        >
                          Report a problem
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}

        {loading && (
          <div className="msg-row assistant">
            <div className="loading-text">Checking the guidelines…</div>
          </div>
        )}

        {quotaExhaustedError && (
          <div className="quota-box" role="alert">
            <p>
              Daily question quota is used up. Resets at {formatResetTime(quotaExhaustedError.resets_at)}.
            </p>
            <button
              type="button"
              className="quota-switch-link"
              onClick={onSwitchToCheckInvoice}
            >
              Switch to Check Invoice (no AI quota needed) &rarr;
            </button>
          </div>
        )}

        {rateLimitError && (
          <div className="error-callout" role="alert">
            <span>{rateLimitError}</span>
          </div>
        )}

        {networkError && (
          <div className="error-callout" role="alert">
            <span>{networkError}</span>
            <button type="button" className="retry-btn" onClick={handleRetry}>
              Retry
            </button>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <div className="chat-form-container">
        <form onSubmit={handleSubmit}>
          <div className="chat-input-box">
            <textarea
              ref={textareaRef}
              className="chat-textarea"
              placeholder={isHealthy ? "Ask a question… (Enter to send, Shift+Enter for newline)" : "Waking up…"}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading || !isHealthy}
              rows={2}
            />

            <div className="chat-form-bottom">
              <span className={`char-counter ${isOverLimit ? 'limit-exceeded' : ''}`}>
                {charCount.toLocaleString()} / {MAX_CHARS.toLocaleString()}
              </span>

              <div className="chat-actions">
                {messages.length > 0 && (
                  <button
                    type="button"
                    className="btn-reset"
                    onClick={handleResetSession}
                    disabled={loading}
                  >
                    Clear
                  </button>
                )}
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={loading || !isHealthy || !input.trim() || isOverLimit}
                >
                  Ask
                </button>
              </div>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
