"use client";

import Link from "next/link";
import { useState } from "react";

import {
  Field,
  FormMessage,
  SubmitButton,
  validateEmail,
} from "@/components/auth/auth-form";
import { AuthShell } from "@/components/auth/auth-shell";
import { friendlyAuthError, getSupabase } from "@/lib/supabase";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const emailError = validateEmail(email);
    setError(emailError);
    if (emailError) return;

    setPending(true);
    setFormError(null);
    const { error: resetError } = await getSupabase().auth.resetPasswordForEmail(
      email.trim(),
      { redirectTo: `${window.location.origin}/reset-password` },
    );
    setPending(false);
    if (resetError) {
      setFormError(friendlyAuthError(resetError.message));
      return;
    }
    // Shown whether or not the address has an account: telling an anonymous
    // caller which emails are registered is an account-enumeration oracle.
    setSent(true);
  }

  return (
    <AuthShell
      title="Forgot your password?"
      subtitle="We will email you a link to set a new one."
      footer={
        <Link href="/login" className="font-medium text-neutral-900 underline underline-offset-4">
          Back to sign in
        </Link>
      }
    >
      {sent ? (
        <FormMessage kind="success">
          If an account exists for {email}, a reset link is on its way.
        </FormMessage>
      ) : (
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          {formError ? <FormMessage kind="error">{formError}</FormMessage> : null}
          <Field
            label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            error={error}
          />
          <SubmitButton pending={pending}>Send reset link</SubmitButton>
        </form>
      )}
    </AuthShell>
  );
}
