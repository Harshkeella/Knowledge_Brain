"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  Field,
  FormMessage,
  SubmitButton,
  validatePassword,
} from "@/components/auth/auth-form";
import { AuthShell } from "@/components/auth/auth-shell";
import { friendlyAuthError, getSupabase } from "@/lib/supabase";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Arriving from the emailed link puts a recovery session in place. Without
  // one there is nothing to update, and the form would fail on submit -- say
  // it up front instead.
  useEffect(() => {
    getSupabase()
      .auth.getSession()
      .then(({ data }) => {
        setReady(Boolean(data.session));
        if (!data.session) {
          setFormError(
            "This reset link has expired or was already used. Request a new one.",
          );
        }
      });
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const nextErrors = {
      password: validatePassword(password),
      confirm: confirm === password ? undefined : "Passwords do not match.",
    };
    setErrors(nextErrors);
    if (Object.values(nextErrors).some(Boolean)) return;

    setPending(true);
    setFormError(null);
    const { error } = await getSupabase().auth.updateUser({ password });
    setPending(false);
    if (error) {
      setFormError(friendlyAuthError(error.message));
      return;
    }
    router.replace("/dashboard/knowledge");
  }

  return (
    <AuthShell
      title="Set a new password"
      subtitle="Choose something you have not used here before."
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {formError ? <FormMessage kind="error">{formError}</FormMessage> : null}
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          disabled={!ready}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={errors.password}
        />
        <Field
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          disabled={!ready}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          error={errors.confirm}
        />
        <SubmitButton pending={pending || !ready}>Update password</SubmitButton>
      </form>
    </AuthShell>
  );
}
