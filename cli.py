from domain.askhelm_logic import ask_askhelm

def main() -> None:
    print("AskHelm MVP")
    print("Type 'exit' to quit.\n")

    while True:
        question = input("AskHelm > ").strip()

        if question.lower() == "exit":
            print("Exiting AskHelm.")
            break

        if not question:
            continue

        try:
            answer = ask_askhelm(question)
            print(f"\n{answer}\n")
        except Exception as e:
            print(f"\nError: {e}\n")

if __name__ == "__main__":
    main()