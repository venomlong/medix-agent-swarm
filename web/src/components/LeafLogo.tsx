type Props = {
  size?: number;
  fill?: string;
};

export function LeafLogo({ size = 18, fill = "#7C9A82" }: Props) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20.5 3.5C11.5 3.6 5.2 9 4.3 18.6c-.1.9.4 1.6 1.3 1.7 9.7.9 15-5.4 15-16.8Z"
        fill={fill}
      />
      <path
        d="M6.2 18.2C8 12.6 12 8.4 17.6 6.4"
        stroke="#F7F4EC"
        strokeWidth="1.1"
        strokeLinecap="round"
      />
    </svg>
  );
}
