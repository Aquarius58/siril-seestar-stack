# siril-seestar-stack

Process raw CFA Seestar images as the first step of a variable star photometry pipeline: debayer and convert to luminance or R/G/B, then stack in user-defined groups.

## Overview

This is a Siril Python script that automates the preprocessing of raw CFA (Color Filter Array) Seestar images for variable star photometry. It handles:

- **Debayering**: Convert raw CFA data to RGB
- **Luminance conversion**: Generate luminance channel from RGB
- **Image stacking**: Stack images in user-defined groups
- **Pipeline integration**: Works as the first step in a complete variable star photometry workflow

## Features

- Batch processing of raw Seestar images
- Configurable stacking groups
- Integration with Siril's Python environment
- Preserves image metadata
- Efficient processing pipeline

## Requirements

- **Siril** (with Python support enabled)
- **Python 3.7+** (Siril's internal Python environment)
- Raw CFA Seestar image files

## Installation

1. Clone this repository or download the script
2. Place the script in your Siril scripts directory or your project folder
3. Ensure you're running it within Siril's Python environment

```bash
git clone https://github.com/Aquarius58/siril-seestar-stack.git
cd siril-seestar-stack
```

## Usage

Run the script from within Siril's Python console or as a Siril Python script:

```python
# Example usage
python siril_seestar_stack.py
```

[Detailed usage documentation coming soon]

## Configuration

[Add configuration options and examples here]

## Contributing

Found a bug or have a feature request? Please open an [issue](https://github.com/Aquarius58/siril-seestar-stack/issues).

For questions, discussions, or contributions, use the [Issues](https://github.com/Aquarius58/siril-seestar-stack/issues) section.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Related Resources

- [Siril Documentation](https://free-astro.org/index.php/Siril)
- Variable star photometry best practices
- Seestar imaging resources

---

**Note**: This script is designed to run only within Siril's Python environment and cannot be executed independently.

