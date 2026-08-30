"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import {
  Divider,
  Field,
  FormMessage,
  GoogleButton,
  SubmitButton,
  validateEmail,
} from "@/components/auth/auth-form";
import { AuthShell } from "@/components/auth/auth-shell";
import { useAuth } from "@/components/auth/auth-provider";
import { friendlyAuthError, getSupabase } from "@/lib/supabase";

const AFTER_SIGN_IN = "/dashboard/knowledge";

function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { session, loading, enabled } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Already signed in: send them on rather than showing a form they cannot
  // usefully submit. Same for a backend running without auth at all.
  useEffect(() => {
    if (!enabled || (!loading && session)) router.replace(AFTER_SIGN_IN);
  }, [enabled, loading, session, router]);

  // The OAuth callback bounces back here with a reason when the user backs out.
  const oauthError = params.get("error");

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const nextErrors = {
      email: validateEmail(email),
      // Not validatePassword: a length rule on sign-in leaks the policy and
      // annoys anyone whose existing password predates it. Let the server say.
      password: password ? undefined : "Enter your password.",
    };
    setErrors(nextErrors);
    if (nextErrors.email || nextErrors.password) return;

    setPending(true);
    setFormError(null);
    const { error } = await getSupabase().auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    setPending(false);
    if (error) {
      setFormError(friendlyAuthError(error.message));
      return;
    }
    router.replace(AFTER_SIGN_IN);
  }

  async function onGoogle() {
    setPending(true);
    setFormError(null);
    const { error } = await getSupabase().auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    });
    if (error) {
      setPending(false);
      setFormError(friendlyAuthError(error.message));
    }
    // On success the browser navigates away; nothing to do here.
  }

  return (
    <AuthShell
      title="Welcome back"
      subtitle="Sign in to your knowledge base."
      footer={
        <>
          New here?{" "}
          <Link href="/signup" className="font-medium text-neutral-900 underline underline-offset-4">
            Create an account
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {formError || oauthError ? (
          <FormMessage kind="error">
            {formError ?? "Google sign-in was cancelled."}
          </FormMessage>
        ) : null}

        <Field
          label="Email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={errors.email}
        />

        <div className="space-y-1.5">
          <Field
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
          />
          <div className="text-right">
            <Link
              href="/forgot-password"
              className="text-xs text-neutral-500 underline-offset-4 hover:text-neutral-900 hover:underline"
            >
              Forgot password?
            </Link>
          </div>
        </div>

        <SubmitButton pending={pending}>Sign in</SubmitButton>
        <Divider />
        <GoogleButton onClick={onGoogle} pending={pending} />
      </form>
    </AuthShell>
  );
}

export default function LoginPage() {
  // useSearchParams needs a Suspense boundary under the App Router.
  return (
    <Suspense fallback={null}>
      <SignInForm />
    </Suspense>
  );
}
