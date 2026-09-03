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

  // In-memory thread_id kept for the session
  const threadIdRef = useRef<string | null>(null);
  const lastSubmittedRef = useRef<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
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
      return; // Prevent client-side
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
        // Handle error responses according to contract
        if (response.status === 429) {
          if (data.resets_at) {
            // Daily question quota exhausted
            setQuotaExhaustedError({
              error: data.error || 'The daily question quota is used up.',
              resets_at: data.resets_at,
            });
          } else {
            // Slowapi rate limit
            setRateLimitError('Slow down, try again in a minute.');
          }
        } else if (response.status === 413) {
          setNetworkError(data.error || `Message exceeds limit of ${MAX_CHARS} characters.`);
        } else {
          setNetworkError(data.error || 'Something went wrong handling that request.');
        }

        // Restore the typed input so the user doesn't lose it
        setInput(trimmed);
        setLoading(false);
        return;
      }

      // Success (200)
      const chatData = data as ChatResponse;
      if (chatData.thread_id) {
        threadIdRef.current = chatData.thread_id;
      }

      const assistantMessage: ChatMessage = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: chatData.answer,
        route: chatData.route,
        citations: chatData.citations || [],
        tokens: chatData.tokens,
        models: chatData.models,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      // Network error
      setNetworkError('Could not reach the server. Please check your connection.');
      setInput(trimmed); // Don't lose typed message
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

  // Format resets_at nicely if available
  const formatResetTime = (isoString?: string) => {
    if (!isoString) return 'midnight UTC';
    try {
      const d = new Date(isoString);
      return `${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (${d.toLocaleDateString()})`;
    } catch {
      return isoString;
    }
  };

  // Route badge helper
  const getRouteBadge = (route?: string | null) => {
    if (!route) return { label: 'General', className: 'general' };
    const r = route.toLowerCase();
    if (r.includes('applicab')) return { label: 'Applicability', className: 'applicability' };
    if (r.includes('field')) return { label: 'Field Check', className: 'field_check' };
    return { label: 'General', className: 'general' };
  };

  // Helper to style "confirm with LHDN" line as a notice
  const renderFormattedAnswer = (text: string) => {
    const lines = text.split('\n');
    const elements: React.ReactNode[] = [];
    let buffer: string[] = [];

    lines.forEach((line, idx) => {
      // Detect "confirm with LHDN"
      const isLhdnNotice = /confirm.*with LHDN/i.test(line);

      if (isLhdnNotice) {
        if (buffer.length > 0) {
          elements.push(
            <div key={`p-${idx}`} style={{ whiteSpace: 'pre-wrap', marginBottom: '0.5rem' }}>
              {buffer.join('\n')}
            </div>
          );
          buffer = [];
        }
        elements.push(
          <div key={`notice-${idx}`} className="lhdn-notice-box">
            <span className="lhdn-notice-icon" aria-hidden="true">&#9888;</span>
            <div>
              <strong>LHDN Verification Notice:</strong> {line.replace(/^["'\s]+|["'\s]+$/g, '')}
            </div>
          </div>
        );
      } else {
        buffer.push(line);
      }
    });

    if (buffer.length > 0) {
      elements.push(
        <div key="p-last" style={{ whiteSpace: 'pre-wrap' }}>
          {buffer.join('\n')}
        </div>
      );
    }

    return elements;
  };

  return (
    <div className="view-container">
      <div className="chat-thread">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <h3>Ask the Compliance Assistant</h3>
            <p>
              Ask any question about Malaysian LHDN e-Invoice guidelines, deadlines, thresholds, or exemptions.
            </p>
            <div className="sample-prompts">
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('What is the exemption threshold for e-Invoice implementation?')}
              >
                &ldquo;What is the exemption threshold for e-Invoice implementation?&rdquo;
              </button>
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('My business started in 2024 with RM2M turnover. When must I implement e-Invoice?')}
              >
                &ldquo;My business started in 2024 with RM2M turnover. When must I implement e-Invoice?&rdquo;
              </button>
              <button
                type="button"
                className="sample-prompt-btn"
                onClick={() => setInput('Can I issue a consolidated e-Invoice for a RM12,000 sale?')}
              >
                &ldquo;Can I issue a consolidated e-Invoice for a RM12,000 sale?&rdquo;
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

            const routeInfo = getRouteBadge(m.route);

            return (
              <div key={m.id} className="msg-row assistant">
                <div className="assistant-card">
                  <div className="msg-header-bar">
                    <span className={`route-badge ${routeInfo.className}`}>
                      {routeInfo.label}
                    </span>
                    {m.tokens !== undefined && m.tokens > 0 && (
                      <span className="msg-meta">
                        {m.tokens} tokens
                      </span>
                    )}
                  </div>

                  <div className="answer-body">
                    {renderFormattedAnswer(m.content)}
                  </div>

                  {m.citations && m.citations.length > 0 && (
                    <div className="citations-block">
                      <div className="citations-heading">Guideline Citations</div>
                      <ul className="citations-list">
                        {m.citations.map((c: Citation, cIdx: number) => (
                          <li key={cIdx} className="citation-item">
                            <span className="citation-bullet">&sect;</span>
                            <span>
                              <strong>{c.doc}</strong> v{c.version} &sect;{c.section}, p{c.page}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}

        {loading && (
          <div className="msg-row assistant">
            <div className="assistant-card" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--muted)' }}>
              <span className="loading-dot" />
              <span>Analyzing official IRBM guidelines...</span>
            </div>
          </div>
        )}

        {quotaExhaustedError && (
          <div className="quota-box" role="alert">
            <p>
              <strong>Daily Quota Exhausted:</strong> {quotaExhaustedError.error}
            </p>
            <p style={{ fontSize: '0.8rem' }}>
              Resets at: <strong>{formatResetTime(quotaExhaustedError.resets_at)}</strong>.
            </p>
            <button
              type="button"
              className="quota-switch-link"
              onClick={onSwitchToCheckInvoice}
            >
              &rarr; Switch to Check Invoice (works without LLM quota)
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
              placeholder={isHealthy ? "Ask a question about e-Invoice rules (Enter to send, Shift+Enter for newline)..." : "Waiting for backend service to become ready..."}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={loading || !isHealthy}
              rows={2}
            />

            <div className="chat-form-bottom">
              <span className={`char-counter ${isOverLimit ? 'limit-exceeded' : ''}`}>
                {charCount.toLocaleString()} / {MAX_CHARS.toLocaleString()}
                {isOverLimit && ' (Limit exceeded)'}
              </span>

              <div className="chat-actions">
                {messages.length > 0 && (
                  <button
                    type="button"
                    className="btn-reset"
                    onClick={handleResetSession}
                    disabled={loading}
                    title="Start a new chat thread"
                  >
                    New Chat
                  </button>
                )}
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={loading || !isHealthy || !input.trim() || isOverLimit}
                >
                  {loading ? 'Asking...' : 'Ask'}
                </button>
              </div>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
